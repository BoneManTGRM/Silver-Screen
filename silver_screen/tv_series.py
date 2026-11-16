"""Season planning and episode production through the existing studio pipeline."""

from __future__ import annotations

import base64
import json
import re
import secrets
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock
from typing import Any

from .ai_video import image_to_data_url, verify_mp4
from .autonomous_studio import (
    active_production, continue_autonomous_production, enqueue_production,
    prepare_autonomous_plan, start_autonomous_production,
)
from .modal_clip_archive import CLIPS_VOLUME
from .modal_renderer import modal_configured
from .runtime import (
    atomic_write_json, create_run_id, load_run_manifest, resolve_runs_root, utc_now, validate_run_id,
)
from .science import GENRES, TONES
from .screenplay_writer import (
    EPISODE_KIND, EPISODE_LENGTHS, IDEA_LIMIT, SCREENPLAY_LENGTHS,
    ScreenplayError, ScreenplayStatusUnavailable, _CAST, _SPEAKER, _STRING, _object,
    _scene_array, _text, poll_writer_result, screenplay_draft, script_sync_text, submit_writer_request,
)
from .video_runtime import load_video_queue

SERIES_ID = re.compile(r"series-[0-9a-f]{16}")
SEASON_COUNTS = range(2, 9)
ARCHIVE_DIR = "tv-series"
MAX_SERIES_BYTES = 4 * 1024 * 1024
MAX_EPISODE_BYTES = 128 * 1024 * 1024
STOPPED_RENDERS = {"shot_retry_budget_exhausted", "provider_call_budget_exhausted", "provider_prompt_rejected"}
_LOCK = RLock()


class SeriesError(ValueError):
    """An invalid series or an operation that needs another user decision."""


def _series_id(value: str) -> str:
    if not SERIES_ID.fullmatch(value):
        raise SeriesError("This series link is invalid.")
    return value


class SeriesStore:
    """Store season files and finalized episodes locally or in the private Modal archive."""

    def __init__(self, *, volume: Any = None) -> None:
        self.root = resolve_runs_root("runs")
        self.directory = self.root / ARCHIVE_DIR
        self.volume = volume

    def _relative(self, series_id: str, leaf: str) -> str:
        return f"{ARCHIVE_DIR}/{_series_id(series_id)}/{leaf}"

    def list(self) -> list[str]:
        if self.volume is None:
            names = [path.parent.name for path in self.directory.glob("*/season.json")]
        else:
            try:
                names = [Path(entry.path).parent.name for entry in self.volume.iterdir(
                    f"/{ARCHIVE_DIR}", recursive=True,
                ) if Path(entry.path).name == "season.json"]
            except Exception as exc:
                from modal.exception import NotFoundError

                status = getattr(exc, "status", None)
                if not isinstance(exc, (FileNotFoundError, NotFoundError)) and getattr(status, "name", "") != "NOT_FOUND":
                    raise
                names = []
        return sorted({name for name in names if SERIES_ID.fullmatch(name)})

    def read(self, series_id: str) -> dict[str, Any]:
        relative = self._relative(series_id, "season.json")
        if self.volume is None:
            with (self.root / relative).open("rb") as source:
                data = source.read(MAX_SERIES_BYTES + 1)
        else:
            data = bytearray()
            for chunk in self.volume.read_file(f"/{relative}"):
                data.extend(chunk)
                if len(data) > MAX_SERIES_BYTES:
                    break
        if len(data) > MAX_SERIES_BYTES:
            raise SeriesError("This season file is too large.")
        state = json.loads(data)
        if not isinstance(state, dict) or state.get("schemaVersion") != 1 or state.get("seriesId") != series_id:
            raise SeriesError("This season file could not be read.")
        return state

    def write(self, series: dict[str, Any]) -> None:
        relative = self._relative(series["seriesId"], "season.json")
        series["updatedAt"] = utc_now()
        data = json.dumps(series, ensure_ascii=False).encode()
        if len(data) > MAX_SERIES_BYTES:
            raise SeriesError("This season file is too large.")
        if self.volume is None:
            atomic_write_json(self.root / relative, series)
        else:
            with self.volume.batch_upload(force=True) as upload:
                upload.put_file(BytesIO(data), f"/{relative}")

    def archive_episode(self, series_id: str, number: int, source: Path, seconds: int) -> None:
        metadata = verify_mp4(source, expected_duration=seconds)
        duration = metadata.get("durationSeconds")
        if duration is None or abs(duration - seconds) > 0.10:
            raise SeriesError("The final MP4 did not match the episode length.")
        if source.stat().st_size > MAX_EPISODE_BYTES:
            raise SeriesError("The episode exceeds the 128 MiB archive limit.")
        relative = self._relative(series_id, f"episode-{int(number)}.mp4")
        if self.volume is not None:
            with source.open("rb") as file, self.volume.batch_upload(force=True) as upload:
                upload.put_file(file, f"/{relative}")
        else:
            with source.open("rb") as file:
                self._cache(self.root / relative, iter(lambda: file.read(1024 * 1024), b""))

    def episode_path(self, series_id: str, number: int) -> Path:
        relative = self._relative(series_id, f"episode-{int(number)}.mp4")
        path = self.root / relative
        if not path.is_file() and self.volume is not None:
            self._cache(path, self.volume.read_file(f"/{relative}"))
        if not path.is_file():
            raise SeriesError("The saved episode MP4 could not be found.")
        return path

    @staticmethod
    def _cache(path: Path, chunks: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=path.parent, delete=False) as output:
            staged = Path(output.name)
            try:
                for chunk in chunks:
                    output.write(chunk)
                    if output.tell() > MAX_EPISODE_BYTES:
                        raise SeriesError("The episode exceeds the 128 MiB download limit.")
                output.close()
                staged.replace(path)
            finally:
                staged.unlink(missing_ok=True)


def studio_series_store() -> SeriesStore:
    if not modal_configured():
        return SeriesStore()
    import modal

    return SeriesStore(volume=modal.Volume.from_name(CLIPS_VOLUME))


def _request(instruction: str, data: dict, schema: dict, seed: int, *, tokens: int) -> dict:
    return {
        "model": "screenplay",
        "messages": [
            {"role": "system", "content": instruction},
            {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
        ],
        "response_format": {"type": "json_schema", "json_schema": {"name": "series", "schema": schema}},
        "temperature": 0.65, "top_p": 0.9, "seed": seed, "max_tokens": tokens,
    }


def create_series(store: SeriesStore, idea: str, count: int, seconds: int, *, title: str = "") -> dict:
    idea = idea.strip()
    if not 12 <= len(idea) <= IDEA_LIMIT:
        raise SeriesError(f"Enter a series premise between 12 and {IDEA_LIMIT:,} characters.")
    if count not in SEASON_COUNTS or seconds not in EPISODE_LENGTHS:
        raise SeriesError("Choose one of the available episode counts and lengths.")
    title = _text(title, 100)
    seed = secrets.randbelow(2**31)
    schema = _object(
        title=_STRING, world=_STRING, arc=_STRING, genre={"enum": list(GENRES)}, tone={"enum": list(TONES)},
        lead=_CAST, support=_CAST,
        episodes=_scene_array(count, title=_STRING, goal=_STRING, outcome=_STRING, hook=_STRING),
    )
    call_id = submit_writer_request(_request(
        "Write an original short-form TV season bible in the language of the brief. Preserve stated names, "
        "setting and established facts. Keep two recurring characters with different names, roles, and "
        "concise appearance notes covering age, hair, face and wardrobe. Describe a repeatable world and a "
        "season arc with a payoff. Return the exact number of episodes. Each episode has a distinct on-screen "
        "goal and outcome within its runtime, and a specific hook that leads into the next goal. In the last "
        "episode pay off the arc. Keep all fields short. Return only the requested JSON.",
        {"series title": title, "premise": idea, "episode count": count, "episode seconds": seconds},
        schema, seed, tokens=1650,
    ))
    series = {
        "schemaVersion": 1, "seriesId": f"series-{secrets.token_hex(8)}", "title": title,
        "premise": idea, "seconds": seconds, "episodeCount": count,
        "createdAt": utc_now(), "seed": seed, "writerJob": call_id,
    }
    with _LOCK:
        store.write(series)
    return series


def _bible(raw: dict, series: dict) -> dict:
    world, arc = _text(raw["world"], 260), _text(raw["arc"], 340)
    cast = {}
    for role in ("lead", "support"):
        card = {key: _text(raw[role][key], limit) for key, limit in (
            ("name", 60), ("role", 100), ("appearance", 220),
        )}
        if not all(card.values()) or not _SPEAKER.fullmatch(card["name"]):
            raise SeriesError("The writer returned incomplete recurring cast details.")
        cast[role] = card
    titles = set()
    episodes = []
    for number, outline in enumerate(raw["episodes"], 1):
        episode = {key: _text(outline[key], limit) for key, limit in (
            ("title", 80), ("goal", 220), ("outcome", 220), ("hook", 190),
        )}
        if not all(episode.values()) or episode["title"].casefold() in titles:
            raise SeriesError("The writer returned an incomplete or repeated episode outline.")
        titles.add(episode["title"].casefold())
        episodes.append({"number": number, **episode})
    title = series["title"] or _text(raw["title"], 100)
    genre, tone = raw["genre"], raw["tone"]
    if (not title or not world or not arc or genre not in GENRES or tone not in TONES
            or cast["lead"]["name"].casefold() == cast["support"]["name"].casefold()
            or len(episodes) != series["episodeCount"]):
        raise SeriesError("The writer returned an incomplete season bible.")
    return {"title": title, "world": world, "arc": arc, "genre": genre, "tone": tone,
            "cast": cast, "episodes": episodes}


def _episode(series: dict, number: int) -> dict:
    episodes = series.get("episodes", [])
    if not 1 <= number <= len(episodes):
        raise SeriesError("Choose an episode from this season.")
    return episodes[number - 1]


def episode_story(series: dict, number: int) -> str:
    episode = _episode(series, number)
    return f"{series['title']}, episode {number}. {episode['title']}. {episode['goal']}"


def _episode_request(series: dict, episode: dict, seed: int) -> dict:
    seconds = series["seconds"]
    clip = SCREENPLAY_LENGTHS[seconds]
    order = episode["number"]
    previous = series["episodes"][:order - 1]
    prior_choice = (previous[-1].get("draft") or {}).get("authoredScript", "") if previous else ""
    return _request(
        "Write one complete narrative TV episode in the language of the series bible. Begin with a concrete "
        "hook, develop the listed goal, show the listed outcome, and finish on the episode hook. Use the exact "
        "recurring names, appearance, setting and established events. Do not rewrite the season or write a "
        "trailer. Each shot is one camera setup and 12–22 words describing visible, physical action. "
        "Use an INT. or EXT. heading. Identify the lead by name when present. Each shot has at most one "
        "short spoken sentence by lead or support; use none and an empty line for a silent shot. "
        "Use the exact number of shots. Return only the JSON.",
        {
            "series": series["title"], "world": series["world"], "season arc": series["arc"],
            "cast": series["cast"], "genre": series["genre"], "tone": series["tone"],
            "episode": {key: episode[key] for key in ("number", "title", "goal", "outcome", "hook")},
            "prior outcomes": [_text(item["outcome"], 95) for item in previous],
            "previous scripted ending": _text(prior_choice.split("\n\n\n")[-1], 240),
            "episode seconds": seconds, "shots": seconds // clip, "seconds per shot": clip,
        },
        _object(scenes=_scene_array(seconds // clip, heading=_STRING, action=_STRING,
                                    speaker={"enum": ["lead", "support", "none"]}, line=_STRING)),
        seed, tokens=2450 if seconds == 120 else 1550,
    )


def start_episode_script(store: SeriesStore, series_id: str, number: int, *, rewrite: bool = False) -> None:
    with _LOCK:
        series = store.read(series_id)
        episode = _episode(series, number)
        if episode.get("writerJob") or episode.get("render") or (episode.get("draft") and not rewrite):
            return
        seed = secrets.randbelow(2**31)
        call_id = submit_writer_request(_episode_request(series, episode, seed))
        episode.update(writerJob=call_id, seed=seed)
        episode.pop("writerError", None)
        store.write(series)


def check_series_script(store: SeriesStore, series_id: str, number: int | None = None) -> bool:
    """Check the same persisted writer call. False means that call is pending."""
    with _LOCK:
        series = store.read(series_id)
        item = _episode(series, number) if number is not None else series
        if not (call_id := item.get("writerJob")):
            return True
        try:
            raw = poll_writer_result(call_id)
            if raw is None:
                return False
            if number is None:
                series.update(_bible(raw, series))
            else:
                # The writer can change the action and dialogue; the bible supplies the fixed cast.
                draft = screenplay_draft(
                    {**raw, **series["cast"], "title": item["title"], "logline": episode_story(series, number),
                     "genre": series["genre"], "tone": series["tone"]},
                    idea=series["premise"], lead=series["cast"]["lead"]["name"],
                    seconds=series["seconds"], seed=item["seed"], kind=EPISODE_KIND,
                )
                draft.update(title=f"{series['title']} · E{number:02d} · {item['title']}", format="short")
                draft["creativeDirection"]["directorNotes"] = (
                    f"Series world: {series['world']} Season arc: {series['arc']} "
                    f"Episode outcome: {item['outcome']} Next hook: {item['hook']}"
                )
                item["draft"] = draft
        except ScreenplayStatusUnavailable:
            raise
        except (ScreenplayError, SeriesError, TypeError, ValueError, KeyError):
            item["writerError"] = "The writer could not finish this draft. Try writing it again."
        item.pop("writerJob", None)
        store.write(series)
        return True


def set_series_photo(store: SeriesStore, series_id: str, photo: Any) -> None:
    reference = image_to_data_url(photo)
    if not reference:
        raise SeriesError("Choose a readable lead photo.")
    with _LOCK:
        series = store.read(series_id)
        series["leadReference"] = reference
        store.write(series)


def _reference(data_url: str) -> BytesIO | None:
    if not data_url:
        return None
    return BytesIO(base64.b64decode(data_url.split(",", 1)[1], validate=True))


def episode_status(store: SeriesStore, episode: dict) -> dict:
    render = episode.get("render") or {}
    run_id = render.get("runId")
    if not run_id:
        return {"status": "draft" if episode.get("draft") else "planned"}
    if render.get("archived"):
        return {"status": "ready"}
    active = active_production(run_id) is not None
    try:
        manifest = load_run_manifest(run_id, "runs")
    except FileNotFoundError:
        return {"status": "rendering" if active else "checkpoint_missing"}
    workspace = Path(manifest["workspace"])
    if not active and not (workspace / "result.json").is_file():
        return {"status": "checkpoint_missing"}
    queue = load_video_queue(workspace / "media") or {}
    return {
        "status": "rendering" if active else "checkpoint",
        "stage": manifest.get("stage"), "runStatus": manifest.get("status"), "metrics": queue.get("metrics") or {},
        "stopReason": queue.get("stopReason"),
        "error": render.get("error") or next((shot.get("lastError") for shot in queue.get("shots") or []
                                              if shot.get("status") != "verified" and shot.get("lastError")), None),
    }


def start_episode_render(
    store: SeriesStore, series_id: str, number: int, *, max_calls: int,
    voice_provider: str = "", restart: bool = False,
) -> str:
    with _LOCK:
        series = store.read(series_id)
        episode = _episode(series, number)
        draft = episode.get("draft")
        if not draft or episode.get("writerJob"):
            raise SeriesError("Write and review the episode script first.")
        required = draft["seconds"] // SCREENPLAY_LENGTHS[draft["seconds"]]
        if not required <= max_calls <= required * 3:
            raise SeriesError(f"Choose a GPU request limit between {required} and {required * 3}.")
        current = episode.get("render") or {}
        existing = current.get("runId")
        if existing:
            validate_run_id(existing)
            if current.get("archived") or active_production(existing):
                return existing
            status = episode_status(store, episode)
            unavailable = status["status"] == "checkpoint_missing"
            stopped = status.get("stopReason") in STOPPED_RENDERS
            if restart:
                if not unavailable and not stopped:
                    raise SeriesError("This render has a resumable checkpoint.")
                existing = None
            elif unavailable:
                raise SeriesError("The render checkpoint is unavailable. A fresh render uses a new GPU request budget.")
        run_id = existing or create_run_id()
        # Preserve the episode's original photo and approved render budget on resume.
        if not existing:
            current = {
                "runId": run_id, "maxCalls": max_calls, "voiceProvider": voice_provider,
                "leadReference": series.get("leadReference", ""),
            }
            episode["render"] = current
            store.write(series)
        config = {
            "projectId": series_id, "qualityProfile": "cinematic", "continuous": True,
            "batchSize": required, "maxProviderCalls": current["maxCalls"],
            "semanticQa": False, "transitionMode": "off",
            "finishAudio": bool(current["voiceProvider"]),
            "voiceProvider": current["voiceProvider"] or "openai",
        }

        def produce() -> dict:
            try:
                export = store.root / run_id / "media" / "episode.mp4"
                saved_job = store.root / run_id / "autonomous_job.json"
                if existing and export.is_file() and saved_job.is_file():
                    outcome = {"job": json.loads(saved_job.read_text(encoding="utf-8"))}
                elif existing:
                    outcome = continue_autonomous_production(run_id, output_root="runs", saved_config=config)
                else:
                    plan = prepare_autonomous_plan(
                        draft, target_runtime_seconds=draft["seconds"],
                        clip_duration_seconds=SCREENPLAY_LENGTHS[draft["seconds"]],
                        max_shots=required, config=config, output_root="runs",
                    )
                    photo = _reference(current["leadReference"])
                    outcome = start_autonomous_production(
                        plan, images=[photo] if photo else [], output_root="runs", run_id=run_id,
                    )
                job = outcome.get("job") or {}
                if job.get("status") == "complete":
                    final = (job.get("artifacts") or {}).get("shortVideo")
                    if not final or Path(final).resolve() != export.resolve():
                        raise SeriesError("The episode footage is saved. Resume the episode to retry the MP4 export.")
                    store.archive_episode(series_id, number, export, draft["seconds"])
                    with _LOCK:
                        latest = store.read(series_id)
                        run = _episode(latest, number)["render"]
                        run.update(archived=True, audioFinished=job.get("audioFinished", False))
                        run.pop("leadReference", None)
                        run.pop("error", None)
                        store.write(latest)
                return outcome
            except Exception as exc:
                with _LOCK:
                    latest = store.read(series_id)
                    run = _episode(latest, number)["render"]
                    run["error"] = (str(exc) if isinstance(exc, SeriesError)
                                    else "The episode could not finish. Check or resume the saved render.")
                    store.write(latest)
                raise

        enqueue_production(run_id, produce)
        return run_id


def episode_subtitles(draft: dict) -> str:
    """Format the reviewed lines as an optional WebVTT track."""
    timed = script_sync_text(draft["authoredScript"], clip_seconds=SCREENPLAY_LENGTHS[draft["seconds"]])
    cues = []
    for line in timed.splitlines():
        timing, text = line.split("] ", 1)
        start, end = timing[1:].split(" --> ")
        cues.append(f"00:{start} --> 00:{end}\n{text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')}")
    return "WEBVTT\n\n" + "\n\n".join(cues) + "\n" if cues else ""
