"""Season planning and episode production through the existing studio pipeline."""

from __future__ import annotations

import base64
import json
import re
import secrets
import shutil
from copy import deepcopy
from hashlib import blake2s
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from threading import RLock
from typing import Any

from .ai_video import VideoGenerationError, image_to_data_url, verify_mp4
from .autonomous_studio import (
    active_production, continue_autonomous_production, enqueue_production,
    prepare_autonomous_plan, start_autonomous_production,
)
from .comfyui import comfyui_ready, video_backend, video_controls_ready
from .modal_clip_archive import CLIPS_VOLUME
from .modal_renderer import modal_configured
from .runtime import (
    atomic_write_json, create_run_id, load_run_manifest, resolve_runs_root, utc_now, validate_run_id,
)
from .science import GENRES, TONES
from .screenplay_writer import (
    EPISODE_KIND, EPISODE_LENGTHS, IDEA_LIMIT, SCREENPLAY_LENGTHS,
    ScreenplayError, ScreenplayStatusUnavailable, _CAST, _SPEAKER, _STRING, _object,
    _SLUGLINE, _scene_array, _text, poll_writer_result, screenplay_draft, script_sync_text, submit_writer_request,
)
from .script_sync import estimate_speech_seconds
from .tv_series_checkpoint import MAX_CHECKPOINT_BYTES, REQUIRED_FILES, build_checkpoint, unpack_checkpoint
from .tv_series_trailer import TRAILER_FORMATS, TRAILER_LENGTHS, export_trailer
from .video_runtime import load_video_queue, save_video_queue
from .visual_quality import VisualQualityError, schedule_quality_retake

SERIES_ID = re.compile(r"series-[0-9a-f]{16}")
TRAILER_ID = re.compile(r"[0-9a-f]{16}")
SEASON_COUNTS = range(2, 9)
VISUAL_DIRECTION_LIMIT = 240
OUTLINE_LIMITS = {"title": 80, "goal": 220, "outcome": 220, "hook": 190}
CAST_LIMITS = {"name": 60, "role": 100, "appearance": 220}
ARCHIVE_DIR = "tv-series"
MAX_SERIES_BYTES = 4 * 1024 * 1024
MAX_EPISODE_BYTES = 128 * 1024 * 1024
GPU_BUDGET_STOP = "provider_call_budget_exhausted"
STOPPED_RENDERS = {"shot_retry_budget_exhausted", GPU_BUDGET_STOP, "provider_prompt_rejected"}
RENDER_MODELS = {"standard": "Standard · Wan 5B on Modal", "wan14": "Cinema · Wan 2.2 14B"}
RENDER_ASPECTS = {"auto": "Default framing", "16:9": "Widescreen · 16:9", "9:16": "Vertical · 9:16"}
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

    def write(self, series: dict[str, Any], *, attachment: tuple[str, Path] | None = None) -> None:
        relative = self._relative(series["seriesId"], "season.json")
        series["updatedAt"] = utc_now()
        data = json.dumps(series, ensure_ascii=False).encode()
        if len(data) > MAX_SERIES_BYTES:
            raise SeriesError("This season file is too large.")
        if self.volume is None:
            if attachment:
                filename, source = attachment
                with source.open("rb") as clip:
                    self._cache(self.root / self._relative(series["seriesId"], filename), iter(lambda: clip.read(1024 * 1024), b""))
            atomic_write_json(self.root / relative, series)
        else:
            with self.volume.batch_upload(force=True) as upload:
                if attachment:
                    filename, source = attachment
                    upload.put_file(source, f"/{self._relative(series['seriesId'], filename)}")
                upload.put_file(BytesIO(data), f"/{relative}")

    def archive_episode(self, series_id: str, number: int, source: Path, seconds: int, *, season: dict | None = None) -> None:
        self._archive_video(series_id, f"episode-{int(number)}.mp4", source, seconds, season=season)

    def archive_trailer(self, series_id: str, trailer_id: str, source: Path, seconds: int, *, season: dict | None = None) -> None:
        self._archive_video(series_id, self._trailer_filename(trailer_id), source, seconds, season=season)

    def _archive_video(self, series_id: str, filename: str, source: Path, seconds: int, *, season: dict | None) -> None:
        metadata = verify_mp4(source, expected_duration=seconds)
        duration = metadata.get("durationSeconds")
        if duration is None or abs(duration - seconds) > 0.10:
            raise SeriesError("The final MP4 did not match the selected length.")
        if source.stat().st_size > MAX_EPISODE_BYTES:
            raise SeriesError("The video exceeds the 128 MiB archive limit.")
        if season is not None:
            self.write(season, attachment=(filename, source))
            return
        relative = self._relative(series_id, filename)
        if self.volume is not None:
            with source.open("rb") as file, self.volume.batch_upload(force=True) as upload:
                upload.put_file(file, f"/{relative}")
        else:
            with source.open("rb") as file:
                self._cache(self.root / relative, iter(lambda: file.read(1024 * 1024), b""))

    def episode_path(self, series_id: str, number: int) -> Path:
        return self._media_path(series_id, f"episode-{int(number)}.mp4")

    def trailer_path(self, series_id: str, trailer_id: str) -> Path:
        return self._media_path(series_id, self._trailer_filename(trailer_id))

    @staticmethod
    def _trailer_filename(trailer_id: str) -> str:
        if not TRAILER_ID.fullmatch(trailer_id):
            raise SeriesError("This trailer link is invalid.")
        return f"trailer-{trailer_id}.mp4"

    def _media_path(self, series_id: str, filename: str) -> Path:
        relative = self._relative(series_id, filename)
        path = self.root / relative
        if not path.is_file() and self.volume is not None:
            self._cache(path, self.volume.read_file(f"/{relative}"))
        if not path.is_file():
            raise SeriesError("The saved media could not be found.")
        return path

    @staticmethod
    def _cache(path: Path, chunks: Any, *, limit: int = MAX_EPISODE_BYTES) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=path.parent, delete=False) as output:
            staged = Path(output.name)
            try:
                for chunk in chunks:
                    output.write(chunk)
                    if output.tell() > limit:
                        raise SeriesError("The saved file exceeds its download limit.")
                output.close()
                staged.replace(path)
            finally:
                staged.unlink(missing_ok=True)

    def save_checkpoint(self, season: dict, run_id: str) -> None:
        run_id = validate_run_id(run_id)
        with TemporaryDirectory(prefix="episode-checkpoint-") as directory:
            snapshot = Path(directory) / "checkpoint.zip"
            build_checkpoint(self.root / run_id, snapshot)
            self.write(season, attachment=(f"checkpoint-{run_id}.zip", snapshot))

    def restore_checkpoint(self, series_id: str, run_id: str) -> None:
        run_id = validate_run_id(run_id)
        workspace = self.root / run_id
        if all((workspace / name).is_file() for name in REQUIRED_FILES):
            return
        if self.volume is None:
            raise SeriesError("The saved episode checkpoint is unavailable.")
        relative = self._relative(series_id, f"checkpoint-{run_id}.zip")
        with TemporaryDirectory(prefix="episode-checkpoint-") as directory:
            snapshot = Path(directory) / "checkpoint.zip"
            self._cache(snapshot, self.volume.read_file(f"/{relative}"), limit=MAX_CHECKPOINT_BYTES)
            unpack_checkpoint(snapshot, workspace)


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


def _new_season(series_id: str, idea: str, count: int, seconds: int, title: str) -> dict:
    idea = idea.strip()
    if not 12 <= len(idea) <= IDEA_LIMIT:
        raise SeriesError(f"Enter a series premise between 12 and {IDEA_LIMIT:,} characters.")
    if count not in SEASON_COUNTS or seconds not in EPISODE_LENGTHS:
        raise SeriesError("Choose one of the available episode counts and lengths.")
    return {
        "schemaVersion": 1, "seriesId": series_id, "title": _text(title, 100),
        "premise": idea, "seconds": seconds, "episodeCount": count, "seasonNumber": 1,
        "createdAt": utc_now(),
    }


def _queue_season(store: SeriesStore, season: dict) -> dict:
    count = season["episodeCount"]
    outline_schema = _scene_array(count, title=_STRING, goal=_STRING, outcome=_STRING, hook=_STRING)
    data = {"series title": season["title"], "premise": season["premise"],
            "episode count": count, "episode seconds": season["seconds"]}
    if season.get("previousSeasonId"):
        instruction = (
            "Continue an existing TV series in the language of the brief. Start the new season from the "
            "consequences of the previous finale. Use the supplied world, recurring cast, style and story facts. "
            "Develop a new season arc from the new premise."
        )
        schema = _object(arc=_STRING, episodes=outline_schema)
        data.update({"season number": season["seasonNumber"], "world": season["world"], "cast": season["cast"],
                     "genre": season["genre"], "tone": season["tone"], "visual direction": season.get("visualDirection", ""),
                     "previous finale": season["previousFinale"]})
    else:
        instruction = (
            "Write an original short-form TV season bible in the language of the brief. Preserve stated names, "
            "setting and established facts. Keep two recurring characters with different names, roles, and "
            "concise appearance notes covering age, hair, face and wardrobe. Describe a repeatable world."
        )
        schema = _object(title=_STRING, world=_STRING, arc=_STRING, genre={"enum": list(GENRES)},
                         tone={"enum": list(TONES)}, lead=_CAST, support=_CAST, episodes=outline_schema)
    instruction += (
        " Return exactly the requested number of distinct episodes. Give each a visible goal and outcome "
        "within its runtime and a specific hook leading into the next episode. In the finale pay off the "
        "season arc. Keep all fields short. Return only the requested JSON."
    )
    season["seed"] = secrets.randbelow(2**31)
    season["writerJob"] = submit_writer_request(_request(instruction, data, schema, season["seed"], tokens=1650))
    season.pop("writerError", None)
    store.write(season)
    return season


def create_series(store: SeriesStore, idea: str, count: int, seconds: int, *, title: str = "") -> dict:
    series = _new_season(f"series-{secrets.token_hex(8)}", idea, count, seconds, title)
    with _LOCK:
        return _queue_season(store, series)


def next_season_id(series_id: str) -> str:
    digest = blake2s(f"next-season:{_series_id(series_id)}".encode(), digest_size=8).hexdigest()
    return f"series-{digest}"


def _scripted_ending(episode: dict) -> str:
    script = (episode.get("draft") or {}).get("authoredScript", "")
    closing = script.rsplit("\n\n\n", 1)[-1].split("\n\n", 2)
    action = _text(" ".join(closing[:2]), 240)
    dialogue = _text(closing[2], 242) if len(closing) > 2 else ""
    return " ".join(part for part in (action, dialogue) if part)


def start_next_season(store: SeriesStore, previous_id: str, idea: str, count: int, seconds: int) -> dict:
    with _LOCK:
        season_id = next_season_id(previous_id)
        try:
            return store.read(season_id)
        except FileNotFoundError:
            pass
        previous = store.read(previous_id)
        if not previous.get("episodes"):
            raise SeriesError("Finish the previous season plan first.")
        finale = previous["episodes"][-1]
        sequel = _new_season(season_id, idea, count, seconds, previous["title"])
        sequel.update({
            key: previous[key] for key in ("world", "cast", "genre", "tone", "visualDirection", "leadReference")
            if key in previous
        })
        sequel.update(
            seasonNumber=previous.get("seasonNumber", 1) + 1,
            previousSeasonId=previous_id,
            previousFinale={"outcome": finale["outcome"], "hook": finale["hook"], "scripted ending": _scripted_ending(finale)},
        )
        return _queue_season(store, sequel)


def retry_season_writer(store: SeriesStore, series_id: str) -> None:
    with _LOCK:
        season = store.read(series_id)
        if season.get("writerError") and not season.get("writerJob") and not season.get("episodes"):
            _queue_season(store, season)


def _cast_card(value: dict) -> dict:
    card = {key: _text(value[key], limit) for key, limit in CAST_LIMITS.items()}
    if not all(card.values()) or not _SPEAKER.fullmatch(card["name"]):
        raise SeriesError("Each recurring character needs a name, role, and appearance description.")
    return card


def _outline(value: dict) -> dict:
    outline = {key: _text(value[key], limit) for key, limit in OUTLINE_LIMITS.items()}
    if not all(outline.values()):
        raise SeriesError("Every episode needs a title, a goal, an outcome, and a hook.")
    return outline


def _bible(raw: dict, series: dict) -> dict:
    inherited = bool(series.get("previousSeasonId"))
    world = series["world"] if inherited else _text(raw["world"], 260)
    arc = _text(raw["arc"], 340)
    cast = series["cast"] if inherited else {role: _cast_card(raw[role]) for role in ("lead", "support")}
    titles = set()
    episodes = []
    for number, outline in enumerate(raw["episodes"], 1):
        episode = _outline(outline)
        if episode["title"].casefold() in titles:
            raise SeriesError("The writer returned an incomplete or repeated episode outline.")
        titles.add(episode["title"].casefold())
        episodes.append({"number": number, **episode})
    title = series["title"] or _text(raw["title"], 100)
    source = series if inherited else raw
    genre, tone = source["genre"], source["tone"]
    if (not title or not world or not arc or genre not in GENRES or tone not in TONES
            or cast["lead"]["name"].casefold() == cast["support"]["name"].casefold()
            or len(episodes) != series["episodeCount"]):
        raise SeriesError("The writer returned an incomplete season bible.")
    return {"title": title, "world": world, "arc": arc, "genre": genre, "tone": tone,
            "cast": cast, "episodes": episodes}


def save_season_bible(store: SeriesStore, series_id: str, *, world: str, arc: str, genre: str, tone: str,
                      cast: dict, visual_direction: str) -> None:
    world, arc = _text(world, 260), _text(arc, 340)
    cast = {role: _cast_card(cast[role]) for role in ("lead", "support")}
    if not world or not arc or genre not in GENRES or tone not in TONES:
        raise SeriesError("Fill in the world and season arc and choose a genre and tone.")
    if cast["lead"]["name"].casefold() == cast["support"]["name"].casefold():
        raise SeriesError("Use a different name for each recurring character.")
    with _LOCK:
        series = store.read(series_id)
        if not series.get("episodes"):
            raise SeriesError("Write the season plan first.")
        series.update(world=world, arc=arc, genre=genre, tone=tone, cast=cast,
                      visualDirection=_text(visual_direction, VISUAL_DIRECTION_LIMIT))
        store.write(series)


def _episode(series: dict, number: int) -> dict:
    episodes = series.get("episodes", [])
    if not 1 <= number <= len(episodes):
        raise SeriesError("Choose an episode from this season.")
    return episodes[number - 1]


def save_episode_outline(store: SeriesStore, series_id: str, number: int, outline: dict) -> None:
    updated = _outline(outline)
    with _LOCK:
        series = store.read(series_id)
        episode = _episode(series, number)
        if episode.get("writerJob") or episode.get("render"):
            raise SeriesError("Edit the outline before writing or rendering starts for this episode.")
        if any(other["number"] != number and other["title"].casefold() == updated["title"].casefold()
               for other in series["episodes"]):
            raise SeriesError("Use a different title for each episode in this season.")
        episode.update(updated)
        store.write(series)


def _episode_request(series: dict, episode: dict, seed: int) -> dict:
    seconds = series["seconds"]
    clip = SCREENPLAY_LENGTHS[seconds]
    order = episode["number"]
    previous = series["episodes"][:order - 1]
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
            "visual direction": series.get("visualDirection", ""),
            "previous season finale": series.get("previousFinale", {}),
            "episode": {key: episode[key] for key in ("number", "title", "goal", "outcome", "hook")},
            "prior outcomes": [_text(item["outcome"], 95) for item in previous],
            "previous scripted ending": _scripted_ending(previous[-1]) if previous else "",
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
        context = {key: series[key] for key in (
            "title", "world", "arc", "cast", "genre", "tone", "seconds", "premise", "seasonNumber", "visualDirection",
        ) if key in series}
        episode.update(writerJob=call_id, seed=seed, writerContext=context)
        episode.pop("writerError", None)
        store.write(series)


def _compile_episode_draft(series: dict, item: dict, raw: dict) -> dict:
    number = item["number"]
    season_number = series.get("seasonNumber", 1)
    story = f"{series['title']}, season {season_number}, episode {number}. {item['title']}. {item['goal']}"
    draft = screenplay_draft(
        {**raw, **series["cast"], "title": item["title"], "logline": story,
         "genre": series["genre"], "tone": series["tone"]},
        idea=series["premise"], lead=series["cast"]["lead"]["name"],
        seconds=series["seconds"], seed=item["seed"], kind=EPISODE_KIND,
    )
    draft.update(title=f"{series['title']} · S{season_number:02d}E{number:02d} · {item['title']}", format="short")
    draft["creativeDirection"]["directorNotes"] = (
        f"Series world: {series['world']} Season arc: {series['arc']} "
        f"Episode outcome: {item['outcome']} Next hook: {item['hook']}"
    )
    if visual_direction := series.get("visualDirection"):
        draft["creativeDirection"]["globalVisualDirection"] = visual_direction
    return draft


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
                item["draft"] = _compile_episode_draft(item.get("writerContext") or series, item, raw)
                item["draft"]["revision"] = secrets.token_hex(8)
                item.pop("shotReferences", None)
        except ScreenplayStatusUnavailable:
            raise
        except (ScreenplayError, SeriesError, TypeError, ValueError, KeyError):
            item["writerError"] = "The writer could not finish this draft. Try writing it again."
        item.pop("writerJob", None)
        item.pop("writerContext", None)
        store.write(series)
        return True


def episode_shots(draft: dict) -> list[dict]:
    """Read the canonical script format, including drafts saved before the shot editor."""
    cast = dict(zip(("lead", "support"), draft["cast"]))
    speakers = {actor["name"].upper(): role for role, actor in cast.items()}
    shots = []
    for block in draft["authoredScript"].split("\n\n\n"):
        heading, action, *dialogue = block.split("\n\n", 2)
        name, _, line = dialogue[0].partition(": ") if dialogue else ("", "", "")
        shots.append({"heading": heading, "action": action, "line": line, "speaker": speakers.get(name, "none")})
    return shots


def save_episode_shot(store: SeriesStore, series_id: str, number: int, shot_number: int, fields: dict, *,
                      expected_revision: str) -> None:
    heading = _text(fields["heading"], 140).upper()
    action = _text(fields["action"], 520)
    line = _text(fields["line"], 180)
    speaker = fields["speaker"]
    if not _SLUGLINE.fullmatch(heading) or len(action.split()) < 6:
        raise SeriesError("Start the heading with INT. or EXT. and describe the visible action in at least six words.")
    if speaker not in {"lead", "support", "none"} or (line and speaker == "none"):
        raise SeriesError("Choose a cast member for the dialogue.")
    with _LOCK:
        series = store.read(series_id)
        episode = _episode(series, number)
        draft = episode.get("draft")
        if not draft or episode.get("writerJob") or episode.get("render"):
            raise SeriesError("Write the episode and save shot edits before rendering.")
        if str(draft.get("revision", draft["seed"])) != expected_revision:
            raise SeriesError("This script was updated. Reopen the shot to edit the current version.")
        clip_seconds = SCREENPLAY_LENGTHS[draft["seconds"]]
        if line and estimate_speech_seconds(line) > clip_seconds - 0.35:
            raise SeriesError(f"Shorten the dialogue to fit the {clip_seconds}-second shot.")
        shots = episode_shots(draft)
        if not 1 <= shot_number <= len(shots):
            raise SeriesError("Choose a shot from the episode.")
        if any(shot["action"].casefold() == action.casefold() for index, shot in enumerate(shots, 1)
               if index != shot_number):
            raise SeriesError("Give this shot its own action so it advances the scene.")
        shots[shot_number - 1] = {"heading": heading, "action": action, "speaker": speaker, "line": line}
        cast = {role: {"name": actor["name"], "role": actor["role"], "appearance": actor["description"]}
                for role, actor in zip(("lead", "support"), draft["cast"])}
        updated = screenplay_draft(
            {**cast, "scenes": shots, "title": draft["title"], "logline": draft["premise"],
             "genre": draft["genre"], "tone": draft["tone"]},
            idea=series["premise"], lead=cast["lead"]["name"], seconds=draft["seconds"],
            seed=draft["seed"], kind=EPISODE_KIND,
        )
        draft.update(authoredScript=updated["authoredScript"], revision=secrets.token_hex(8))
        draft["shotDirection"]["shotPromptOverrides"] = updated["shotDirection"]["shotPromptOverrides"]
        store.write(series)


def episode_shot_reference(store: SeriesStore, series_id: str, episode: dict, shot_number: int) -> Path | None:
    token = (episode.get("shotReferences") or {}).get(str(shot_number))
    if token is None:
        return None
    if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{32}", token):
        raise SeriesError("This starting frame could not be read.")
    try:
        return store._media_path(series_id, _shot_reference_filename(episode["number"], shot_number, token))
    except (OSError, SeriesError) as exc:
        raise SeriesError(f"The saved starting frame for shot {shot_number} could not be opened.") from exc


def _shot_reference_filename(number: int, shot_number: int, token: str) -> str:
    return f"episode-{int(number)}-shot-{int(shot_number)}-{token}.jpg"


def save_episode_shot_reference(
    store: SeriesStore, series_id: str, number: int, shot_number: int, photo: Any, *, expected_revision: str,
) -> None:
    data = _reference(image_to_data_url(photo) or "")
    if photo is not None and data is None:
        raise SeriesError("The starting image could not be read or fit within the render upload limit.")
    with _LOCK:
        series = store.read(series_id)
        episode = _episode(series, number)
        draft = episode.get("draft")
        if not draft or episode.get("writerJob") or episode.get("render"):
            raise SeriesError("Set starting frames after writing the script and before rendering.")
        if str(draft.get("revision", draft["seed"])) != expected_revision:
            raise SeriesError("This script was updated. Reopen the shot to edit the current version.")
        if not 1 <= shot_number <= len(episode_shots(draft)):
            raise SeriesError("Choose a shot from the episode.")
        frames = episode.setdefault("shotReferences", {})
        if data is None:
            frames.pop(str(shot_number), None)
            store.write(series)
            return
        payload = data.getvalue()
        token = blake2s(payload, digest_size=16).hexdigest()
        frames[str(shot_number)] = token
        with TemporaryDirectory(prefix="series-shot-") as directory:
            path = Path(directory) / "reference.jpg"
            path.write_bytes(payload)
            store.write(series, attachment=(_shot_reference_filename(number, shot_number, token), path))


def _episode_shot_references_available() -> bool:
    return video_backend() == "replicate" or (
        video_backend() == "comfyui" and (modal_configured() or comfyui_ready("reference"))
    )


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


def episode_framing_available() -> bool:
    return video_backend() == "comfyui" and (
        modal_configured() or (video_controls_ready("video") and video_controls_ready("reference"))
    )


def _opening_files(root: Path, queue: dict) -> dict:
    first = (queue.get("shots") or [{}])[0]
    paths = {}
    if first.get("status") == "verified" and first.get("path"):
        paths["current"] = str(root / first["path"])
    if prior := queue.get("openingRetakeFallback"):
        paths["previous"] = str(root / prior["path"])
    return {kind: path for kind, path in paths.items() if Path(path).is_file()}


def _prepare_opening_retake(root: Path, run_id: str, directive: str) -> None:
    queue = load_video_queue(root) or {}
    first = deepcopy(queue["shots"][0])
    try:
        scheduled = schedule_quality_retake(run_id, first["id"], reason=directive)
    except VisualQualityError as exc:
        raise SeriesError(str(exc)) from exc
    queued = scheduled["queue"]
    queued["shots"][0]["openingRetakeNotes"] = directive
    queued["openingRetakeFallback"] = {
        "shot": first, "path": Path(scheduled["archivedCandidate"]).relative_to(root).as_posix(),
    }
    save_video_queue(root, queued)


def _save_episode_checkpoint(store: SeriesStore, season: dict, episode: dict) -> None:
    if store.volume is None:
        store.write(season)
        return
    render = episode["render"]
    workspace = store.root / render["runId"]
    queue = load_video_queue(workspace / "media") or {}
    if not queue or not (workspace / "result.json").is_file():
        store.write(season)
        return
    render["savedCheckpoint"] = {
        "metrics": queue.get("metrics") or {}, "stopReason": queue.get("stopReason"),
        "runStatus": queue.get("status"),
        "hasOpening": bool(render.get("reviewOpening") and _opening_files(workspace / "media", queue)),
    }
    store.save_checkpoint(season, render["runId"])


def reopen_episode_checkpoint(store: SeriesStore, series_id: str, number: int) -> None:
    with _LOCK:
        render = _episode(store.read(series_id), number).get("render") or {}
        if not render.get("savedCheckpoint") or active_production(render["runId"]):
            raise SeriesError("A saved episode checkpoint is required.")
        store.restore_checkpoint(series_id, render["runId"])


def keep_previous_opening(store: SeriesStore, series_id: str, number: int) -> None:
    with _LOCK:
        season = store.read(series_id)
        episode = _episode(season, number)
        render = episode.get("render") or {}
        run_id = validate_run_id(render.get("runId", ""))
        if not render.get("reviewOpening") or active_production(run_id):
            raise SeriesError("Wait for the opening retake to finish before choosing a take.")
        root = store.root / run_id / "media"
        queue = load_video_queue(root) or {}
        prior = queue.get("openingRetakeFallback")
        if not prior or queue["shots"][0].get("status") == "submitted":
            raise SeriesError("Check the pending retake before choosing a take.")
        current = queue["shots"][0]
        accepted = prior["shot"]
        # Both attempts still count against the approved total.
        accepted["attempts"] = current["attempts"]
        accepted["visualQualityRetakeHistory"] = current.get("visualQualityRetakeHistory", [])
        if current.get("status") == "verified" and current.get("path"):
            alternate = (root / prior["path"]).with_name(f"alternate-{current['attempts']:02d}.mp4")
            shutil.copy2(root / current["path"], alternate)
        destination = root / accepted["path"]
        with NamedTemporaryFile(dir=destination.parent, delete=False) as output:
            staged = Path(output.name)
            try:
                with (root / prior["path"]).open("rb") as source:
                    shutil.copyfileobj(source, output)
                output.close()
                staged.replace(destination)
            finally:
                staged.unlink(missing_ok=True)
        queue["shots"][0] = accepted
        queue.pop("openingRetakeFallback")
        queue.update(status="partial", stopReason="batch_checkpoint")
        save_video_queue(root, queue)
        render.pop("error", None)
        _save_episode_checkpoint(store, season, episode)


def episode_status(store: SeriesStore, episode: dict) -> dict:
    render = episode.get("render") or {}
    run_id = render.get("runId")
    if not run_id:
        return {"status": "draft" if episode.get("draft") else "planned"}
    if render.get("archived"):
        return {"status": "ready"}
    active = active_production(run_id) is not None
    saved = render.get("savedCheckpoint")
    missing = {"status": "checkpoint", "restorable": True, **saved} if saved else {"status": "checkpoint_missing"}
    try:
        manifest = load_run_manifest(run_id, "runs")
    except FileNotFoundError:
        return {"status": "rendering"} if active else missing
    workspace = Path(manifest["workspace"])
    if not active and not all((workspace / name).is_file() for name in REQUIRED_FILES):
        return missing
    media = workspace / "media"
    queue = load_video_queue(media) or {}
    opening = _opening_files(media, queue) if render.get("reviewOpening") and not active else {}
    return {
        "status": "rendering" if active else "checkpoint",
        "stage": manifest.get("stage"), "runStatus": manifest.get("status"), "metrics": queue.get("metrics") or {},
        "stopReason": queue.get("stopReason"),
        "opening": opening,
        "error": render.get("error") or next((shot.get("lastError") for shot in queue.get("shots") or []
                                              if shot.get("status") != "verified" and shot.get("lastError")), None),
    }


def start_episode_render(
    store: SeriesStore, series_id: str, number: int, *, max_calls: int,
    voice_provider: str = "", restart: bool = False, render_model: str = "standard", aspect: str = "auto",
    review_opening: bool = False, approve_opening: bool = False, retake: str | None = None,
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
            if status.get("restorable") and not restart:
                store.restore_checkpoint(series_id, existing)
                status = episode_status(store, episode)
            unavailable = status["status"] == "checkpoint_missing"
            stopped = status.get("stopReason") in STOPPED_RENDERS
            if restart:
                if not unavailable and not stopped:
                    raise SeriesError("This render has a resumable checkpoint.")
                existing = None
            elif unavailable:
                raise SeriesError("The render checkpoint is unavailable. A fresh render uses a new GPU request budget.")
            elif status.get("stopReason") == GPU_BUDGET_STOP:
                used = int((status.get("metrics") or {}).get("providerCalls", current["maxCalls"]))
                if max_calls <= used:
                    raise SeriesError(f"Set a total limit above the {used} GPU requests already used.")
                if max_calls != current["maxCalls"]:
                    current["maxCalls"] = max_calls
                    store.write(series)
            if existing and current.get("reviewOpening"):
                opening = status.get("opening") or {}
                if approve_opening:
                    if not opening.get("current"):
                        raise SeriesError("Review a completed opening shot first.")
                    current["reviewOpening"] = False
                    store.write(series)
                elif retake is not None:
                    if not opening.get("current"):
                        raise SeriesError("Review a completed opening shot before requesting a retake.")
                    used = int((status.get("metrics") or {}).get("providerCalls", 0))
                    if used >= current["maxCalls"]:
                        raise SeriesError("The approved GPU request limit has been reached.")
                    _prepare_opening_retake(store.root / existing / "media", existing, _text(retake, 300))
                elif opening.get("current"):
                    raise SeriesError("Approve the opening shot to render the rest of this episode.")
            if existing and current.pop("savedCheckpoint", None) is not None:
                # A previous snapshot cannot account for requests made by the next invocation.
                store.write(series)
        run_id = existing or create_run_id()
        # Preserve the episode's original photo and approved render budget on resume.
        shot_references = {}
        if not existing:
            if episode.get("shotReferences"):
                if not _episode_shot_references_available():
                    raise SeriesError("Configure image-to-video rendering to use the saved starting frames.")
                for shot_number in range(1, required + 1):
                    if frame := episode_shot_reference(store, series_id, episode, shot_number):
                        shot_references[shot_number] = frame
            if render_model not in RENDER_MODELS or aspect not in RENDER_ASPECTS:
                raise SeriesError("Choose a supported episode model and frame format.")
            if render_model == "wan14" and not (video_backend() == "comfyui" and modal_configured()):
                raise SeriesError("Wan 2.2 14B episode rendering requires the Modal renderer.")
            if aspect != "auto" and not episode_framing_available():
                raise SeriesError("The video backend needs frame size controls for this format.")
            current = {
                "runId": run_id, "maxCalls": max_calls, "voiceProvider": voice_provider,
                "leadReference": series.get("leadReference", ""),
                "model": render_model, "aspect": aspect, "reviewOpening": review_opening,
            }
            episode["render"] = current
            store.write(series)
        review = bool(current.get("reviewOpening"))
        config = {
            "projectId": series_id, "qualityProfile": "cinematic", "continuous": not review,
            "batchSize": 1 if review else required, "maxProviderCalls": current["maxCalls"],
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
                    brief = deepcopy(draft)
                    if shot_references:
                        brief["shotDirection"]["episodeStartingFrames"] = sorted(shot_references)
                    if render_model != "standard" or aspect != "auto":
                        brief["shotDirection"]["episodeVideo"] = {"model": render_model, "aspect": aspect}
                    plan = prepare_autonomous_plan(
                        brief, target_runtime_seconds=draft["seconds"],
                        clip_duration_seconds=SCREENPLAY_LENGTHS[draft["seconds"]],
                        max_shots=required, config=config, output_root="runs",
                    )
                    photo = _reference(current["leadReference"])
                    outcome = start_autonomous_production(
                        plan, images=[photo] if photo else [], output_root="runs", run_id=run_id,
                        shot_references=shot_references,
                    )
                job = outcome.get("job") or {}
                with _LOCK:
                    latest = store.read(series_id)
                    updated = _episode(latest, number)
                    run = updated["render"]
                    run.pop("error", None)
                    if job.get("status") == "complete":
                        final = (job.get("artifacts") or {}).get("shortVideo")
                        if not final or Path(final).resolve() != export.resolve():
                            raise SeriesError("The episode footage is saved. Resume the episode to retry the MP4 export.")
                        run.update(archived=True, audioFinished=job.get("audioFinished", False))
                        run.pop("leadReference", None)
                        run.pop("savedCheckpoint", None)
                        store.archive_episode(series_id, number, export, draft["seconds"], season=latest)
                    else:
                        _save_episode_checkpoint(store, latest, updated)
                return outcome
            except Exception as exc:
                with _LOCK:
                    latest = store.read(series_id)
                    updated = _episode(latest, number)
                    run = updated["render"]
                    run["error"] = (str(exc) if isinstance(exc, SeriesError)
                                    else "The episode could not finish. Check or resume the saved render.")
                    try:
                        _save_episode_checkpoint(store, latest, updated)
                    except Exception:
                        run.pop("savedCheckpoint", None)
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


def cut_season_trailer(store: SeriesStore, series_id: str, episode_numbers: list[int], *, seconds: int,
                       frame_format: str, tagline: str = "") -> dict:
    selected = sorted(set(episode_numbers))
    if not selected or seconds not in TRAILER_LENGTHS or frame_format not in TRAILER_FORMATS:
        raise SeriesError("Choose finished episodes, a trailer length, and a frame format.")
    series = store.read(series_id)
    episodes = [_episode(series, number) for number in selected]
    if any(not (episode.get("render") or {}).get("archived") for episode in episodes):
        raise SeriesError("Choose episodes with saved MP4s.")
    tagline = _text(tagline, 80)
    settings = {"title": series["title"], "seasonNumber": series.get("seasonNumber", 1),
                "episodeNumbers": selected, "seconds": seconds, "frameFormat": frame_format, "tagline": tagline}
    signature = json.dumps([settings, [ep["render"]["runId"] for ep in episodes]], sort_keys=True).encode()
    trailer_id = blake2s(signature, digest_size=8).hexdigest()
    if existing := (series.get("trailers") or {}).get(trailer_id):
        return existing
    sources = [(store.episode_path(series_id, ep["number"]), ep["draft"]["seconds"]) for ep in episodes]
    with TemporaryDirectory(prefix="silver-screen-season-trailer-") as temporary:
        try:
            video = export_trailer(
                sources, Path(temporary) / "trailer.mp4", seconds=seconds,
                shot_seconds=SCREENPLAY_LENGTHS[series["seconds"]], frame_format=frame_format,
                title=settings["title"], season_number=settings["seasonNumber"], tagline=tagline,
            )
            with _LOCK:
                series = store.read(series_id)
                trailers = series.setdefault("trailers", {})
                if trailer_id not in trailers:
                    trailers[trailer_id] = {**settings, "id": trailer_id, "createdAt": utc_now()}
                    store.archive_trailer(series_id, trailer_id, video, seconds, season=series)
                return trailers[trailer_id]
        except VideoGenerationError as exc:
            raise SeriesError(str(exc)) from exc
