"""Command-line operations for Silver-Screen."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from .health import health_report
from .pipeline import (
    BriefValidationError,
    PipelineError,
    resume_video_run,
    run_pipeline,
    validate_brief,
    video_run_status,
)
from .runtime import list_resumable_runs, list_runs
from .science import FORMATS, GENRES, TONES
from .voice_studio import VoiceStudioError, attach_voice_to_run


def _load_json(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise BriefValidationError(
            "brief file must contain a JSON object"
        )
    return payload


def _add_brief_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--brief", help="Path to a JSON production brief"
    )
    parser.add_argument("--premise", help="Story premise")
    parser.add_argument("--title", help="Optional title")
    parser.add_argument(
        "--genre", choices=sorted(GENRES), help="Story genre"
    )
    parser.add_argument(
        "--tone", choices=sorted(TONES), help="Story tone"
    )
    parser.add_argument(
        "--format",
        dest="fmt",
        choices=sorted(FORMATS),
        help="Production format",
    )
    parser.add_argument(
        "--seed", type=int, help="Deterministic generation seed"
    )


def _add_video_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--target-runtime-seconds",
        type=int,
        help="Requested assembled AI-film runtime, up to 5400 seconds",
    )
    parser.add_argument(
        "--video-max-shots",
        type=int,
        help="Hard ceiling on paid generated clips",
    )
    parser.add_argument(
        "--video-batch-size",
        type=int,
        help="New paid predictions processed before checkpointing",
    )
    parser.add_argument(
        "--video-max-retries",
        type=int,
        help="TGRM repair retries per video shot",
    )
    parser.add_argument(
        "--video-max-provider-calls",
        type=int,
        help="Whole-production video-provider call budget",
    )
    parser.add_argument(
        "--video-max-spend-usd",
        type=float,
        help="Estimated spend gate; 0 disables cost gating",
    )
    parser.add_argument(
        "--video-cost-per-second-usd",
        type=float,
        help="Operator-supplied provider price used for estimates",
    )
    parser.add_argument(
        "--video-continuous",
        action="store_true",
        help="Continue until completion or a budget/repair gate",
    )
    parser.add_argument(
        "--no-video-continuity",
        action="store_true",
        help="Disable final-frame continuity chaining",
    )


def _add_voice_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--enable-voices",
        action="store_true",
        help="Generate or mix voices for verified AI-video clips",
    )
    parser.add_argument(
        "--voice-provider",
        choices=["openai", "elevenlabs", "manual"],
        default="openai",
    )
    parser.add_argument(
        "--voice-mode",
        choices=["dialogue+narration", "dialogue", "narration"],
        default="dialogue+narration",
    )
    parser.add_argument("--voice-model")
    parser.add_argument("--lead-voice", default="coral")
    parser.add_argument("--supporting-voice", default="onyx")
    parser.add_argument("--narrator-voice", default="cedar")
    parser.add_argument(
        "--voice-instructions",
        default=(
            "Deliver an expressive cinematic performance with clear diction."
        ),
    )
    parser.add_argument(
        "--voice-speed", type=float, default=1.0
    )
    parser.add_argument(
        "--voice-max-retries", type=int, default=1
    )
    parser.add_argument(
        "--preserve-source-audio",
        action="store_true",
        help="Mix generated ambience quietly beneath dialogue",
    )
    parser.add_argument(
        "--no-subtitles",
        action="store_true",
        help="Disable SRT subtitle generation",
    )
    parser.add_argument(
        "--voice-track",
        "--voice",
        dest="voice_tracks",
        action="append",
        default=[],
        help=(
            "Authorized finished voice track; repeatable. "
            "Use names such as shot_0001.wav for exact matching."
        ),
    )
    parser.add_argument(
        "--voice-authorized",
        action="store_true",
        help="Confirm authorization for uploaded/custom voice material",
    )
    parser.add_argument("--custom-voice-id")
    parser.add_argument("--custom-voice-name")
    parser.add_argument("--voice-sample")
    parser.add_argument("--voice-consent")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="silver-screen",
        description=(
            "Deterministic story production with TGRM narrative repair, "
            "resumable AI video, authorized voices, subtitles, and final assembly."
        ),
    )
    subparsers = parser.add_subparsers(
        dest="command", required=True
    )

    run_parser = subparsers.add_parser(
        "run", help="Execute a production run"
    )
    _add_brief_arguments(run_parser)
    run_parser.add_argument(
        "--image",
        action="append",
        default=[],
        help="Authorized portrait/reference image path; repeatable",
    )
    run_parser.add_argument(
        "--output",
        choices=["runs"],
        default="runs",
        help="Allowlisted run-storage alias",
    )
    run_parser.add_argument(
        "--media",
        choices=[
            "off",
            "cards",
            "preview",
            "preview-film",
            "ai-video",
        ],
        default="cards",
        help="Media output level",
    )
    run_parser.add_argument(
        "--max-chapters", type=int, default=4
    )
    run_parser.add_argument("--max-cycles", type=int)
    run_parser.add_argument("--energy-budget", type=int)
    _add_video_arguments(run_parser)
    _add_voice_arguments(run_parser)
    run_parser.add_argument(
        "--no-persist", action="store_true"
    )
    run_parser.add_argument(
        "--json",
        action="store_true",
        help="Print a machine-readable run summary",
    )

    resume_parser = subparsers.add_parser(
        "resume-video",
        help="Continue a checkpointed AI-film and its persisted voice plan",
    )
    resume_parser.add_argument("run_id")
    resume_parser.add_argument(
        "--output", choices=["runs"], default="runs"
    )
    _add_video_arguments(resume_parser)
    _add_voice_arguments(resume_parser)
    resume_parser.add_argument(
        "--json", action="store_true"
    )

    voice_parser = subparsers.add_parser(
        "voice-run",
        help="Add, replace, or resume voices on an existing AI-film run",
    )
    voice_parser.add_argument("run_id")
    voice_parser.add_argument(
        "--output", choices=["runs"], default="runs"
    )
    _add_voice_arguments(voice_parser)
    voice_parser.add_argument(
        "--json", action="store_true"
    )

    status_parser = subparsers.add_parser(
        "video-status",
        help="Inspect a checkpointed AI-film production",
    )
    status_parser.add_argument("run_id")
    status_parser.add_argument(
        "--output", choices=["runs"], default="runs"
    )
    status_parser.add_argument(
        "--json", action="store_true"
    )

    validate_parser = subparsers.add_parser(
        "validate", help="Validate and normalize a brief"
    )
    _add_brief_arguments(validate_parser)

    health_parser = subparsers.add_parser(
        "health", help="Inspect runtime capabilities"
    )
    health_parser.add_argument(
        "--output", choices=["runs"], default="runs"
    )
    health_parser.add_argument("--json", action="store_true")

    list_parser = subparsers.add_parser(
        "list", help="List recent durable runs"
    )
    list_parser.add_argument(
        "--output", choices=["runs"], default="runs"
    )
    list_parser.add_argument("--limit", type=int, default=20)
    list_parser.add_argument("--json", action="store_true")

    resumable_parser = subparsers.add_parser(
        "list-resumable",
        help="List AI-film runs that can be continued",
    )
    resumable_parser.add_argument(
        "--output", choices=["runs"], default="runs"
    )
    resumable_parser.add_argument("--limit", type=int, default=20)
    resumable_parser.add_argument("--json", action="store_true")
    return parser


def _brief_from_args(args: argparse.Namespace) -> dict[str, Any]:
    brief = _load_json(getattr(args, "brief", None))
    overrides = {
        "premise": getattr(args, "premise", None),
        "title": getattr(args, "title", None),
        "genre": getattr(args, "genre", None),
        "tone": getattr(args, "tone", None),
        "format": getattr(args, "fmt", None),
        "seed": getattr(args, "seed", None),
    }
    for key, value in overrides.items():
        if value is not None:
            brief[key] = value
    return brief


def _voice_request(
    args: argparse.Namespace,
) -> list[dict[str, Any]] | None:
    if not bool(getattr(args, "enable_voices", False)):
        return None
    provider = str(getattr(args, "voice_provider", "openai"))
    tracks = list(getattr(args, "voice_tracks", []) or [])
    request: dict[str, Any] = {
        "enabled": True,
        "provider": provider,
        "mode": getattr(
            args, "voice_mode", "dialogue+narration"
        ),
        "model": getattr(args, "voice_model", None),
        "lead_voice": (
            getattr(args, "custom_voice_id", None)
            or getattr(args, "lead_voice", "coral")
        ),
        "supporting_voice": getattr(
            args, "supporting_voice", "onyx"
        ),
        "narrator_voice": getattr(
            args, "narrator_voice", "cedar"
        ),
        "instructions": getattr(
            args, "voice_instructions", ""
        ),
        "speed": getattr(args, "voice_speed", 1.0),
        "max_retries_per_line": getattr(
            args, "voice_max_retries", 1
        ),
        "preserve_source_audio": bool(
            getattr(args, "preserve_source_audio", False)
        ),
        "subtitles": not bool(
            getattr(args, "no_subtitles", False)
        ),
        "authorization_confirmed": bool(
            getattr(args, "voice_authorized", False)
        ),
        "custom_voice": bool(
            getattr(args, "voice_sample", None)
            or getattr(args, "voice_consent", None)
        ),
        "custom_voice_id": (
            getattr(args, "custom_voice_id", None) or ""
        ),
        "custom_voice_name": (
            getattr(args, "custom_voice_name", None)
            or "Silver Screen Voice"
        ),
        "voice_sample": getattr(
            args, "voice_sample", None
        ),
        "consent_recording": getattr(
            args, "voice_consent", None
        ),
        "manual_tracks": tracks,
        "allow_original_fallback": True,
    }
    return [request]


def _progress(stage: str, percent: int, message: str) -> None:
    print(
        f"[{percent:3d}%] {stage}: {message}",
        file=sys.stderr,
    )


def _run_summary(result: dict[str, Any]) -> dict[str, Any]:
    state = result.get("state") or {}
    media = result.get("media") or {}
    voice = media.get("voice") or {}
    return {
        "status": result.get("status"),
        "runId": (result.get("run") or {}).get("id"),
        "workspace": (result.get("run") or {}).get(
            "workspace"
        ),
        "title": state.get("title"),
        "format": state.get("format"),
        "sceneCount": len(state.get("scenes") or []),
        "metrics": result.get("metrics") or {},
        "msil": result.get("msil") or {},
        "videoMetrics": media.get("metrics") or {},
        "videoMsil": media.get("msil") or {},
        "videoStopReason": media.get("stopReason"),
        "resumeRequired": media.get("resumeRequired", False),
        "finalVideo": media.get("final_video_path"),
        "partialVideo": media.get("partial_video_path"),
        "voiceStatus": voice.get("status"),
        "voiceMetrics": voice.get("metrics") or {},
        "voiceError": voice.get("error"),
        "subtitles": voice.get("subtitles_path"),
        "warnings": result.get("warnings") or [],
        "artifacts": result.get("artifacts") or {},
        "timings": result.get("timings") or {},
    }


def _video_kwargs(
    args: argparse.Namespace,
) -> dict[str, Any]:
    return {
        "target_runtime_seconds": getattr(
            args, "target_runtime_seconds", None
        ),
        "video_max_shots": getattr(
            args, "video_max_shots", None
        ),
        "video_batch_size": getattr(
            args, "video_batch_size", None
        ),
        "video_max_retries": getattr(
            args, "video_max_retries", None
        ),
        "video_max_provider_calls": getattr(
            args, "video_max_provider_calls", None
        ),
        "video_max_spend_usd": getattr(
            args, "video_max_spend_usd", None
        ),
        "video_cost_per_second_usd": getattr(
            args, "video_cost_per_second_usd", None
        ),
        "video_continuous": bool(
            getattr(args, "video_continuous", False)
        ),
        "video_use_continuity": not bool(
            getattr(args, "no_video_continuity", False)
        ),
    }


def _print_summary(
    summary: dict[str, Any],
    *,
    as_json: bool,
) -> None:
    if as_json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return
    print(
        f"Status: {str(summary.get('status')).upper()} | "
        f"{summary.get('title') or 'Untitled'} "
        f"[{summary.get('runId')}]"
    )
    if summary.get("workspace"):
        print(f"Workspace: {summary['workspace']}")
    video = summary.get("videoMetrics") or {}
    if video:
        print(
            "Video: "
            f"{video.get('verifiedShots', 0)}/"
            f"{video.get('plannedShots', 0)} clips, "
            f"{video.get('verifiedSeconds', 0)} verified seconds"
        )
    voice = summary.get("voiceMetrics") or {}
    if summary.get("voiceStatus"):
        print(
            "Voice: "
            f"{summary.get('voiceStatus')} | "
            f"{voice.get('generatedLines', 0)}/"
            f"{voice.get('plannedLines', 0)} lines"
        )
    if summary.get("resumeRequired"):
        print(
            "Resume: silver-screen resume-video "
            f"{summary.get('runId')}"
        )
    final_video = summary.get("finalVideo")
    partial_video = summary.get("partialVideo")
    if final_video:
        print(f"Final video: {final_video}")
    elif partial_video:
        print(f"Partial video: {partial_video}")
    if summary.get("subtitles"):
        print(f"Subtitles: {summary['subtitles']}")
    if summary.get("voiceError"):
        print(
            f"Voice error: {summary['voiceError']}",
            file=sys.stderr,
        )
    bundle = (summary.get("artifacts") or {}).get("bundle")
    if bundle:
        print(f"Bundle: {bundle}")
    for warning in summary.get("warnings") or []:
        print(f"Warning: {warning}", file=sys.stderr)


def _handle_run(args: argparse.Namespace) -> int:
    brief = _brief_from_args(args)
    result = run_pipeline(
        brief,
        images=args.image,
        voices=_voice_request(args),
        output_root=args.output,
        persist=not args.no_persist,
        render_media=args.media != "off",
        video_mode=(
            "cards" if args.media == "off" else args.media
        ),
        max_chapters=args.max_chapters,
        max_cycles=args.max_cycles,
        energy_budget=args.energy_budget,
        progress=None if args.json else _progress,
        **_video_kwargs(args),
    )
    _print_summary(
        _run_summary(result),
        as_json=args.json,
    )
    return 0


def _handle_resume(args: argparse.Namespace) -> int:
    result = resume_video_run(
        args.run_id,
        output_root=args.output,
        batch_size=args.video_batch_size,
        continuous=args.video_continuous,
        max_retries=args.video_max_retries,
        max_provider_calls=args.video_max_provider_calls,
        max_spend_usd=args.video_max_spend_usd,
        cost_per_second_usd=args.video_cost_per_second_usd,
        use_continuity=not args.no_video_continuity,
        progress=None if args.json else _progress,
    )
    if args.enable_voices:
        result = attach_voice_to_run(
            args.run_id,
            _voice_request(args),
            output_root=args.output,
        )
    _print_summary(
        _run_summary(result),
        as_json=args.json,
    )
    return 0


def _handle_voice_run(args: argparse.Namespace) -> int:
    if not args.enable_voices:
        raise VoiceStudioError(
            "voice-run requires --enable-voices and a configured voice source"
        )
    result = attach_voice_to_run(
        args.run_id,
        _voice_request(args),
        output_root=args.output,
    )
    _print_summary(
        _run_summary(result),
        as_json=args.json,
    )
    return 0


def _handle_video_status(args: argparse.Namespace) -> int:
    status = video_run_status(args.run_id, args.output)
    if args.json:
        print(json.dumps(status, indent=2, ensure_ascii=False))
    else:
        video = status["video"]
        metrics = video.get("metrics") or {}
        print(
            f"{args.run_id} | {video.get('status')} | "
            f"{metrics.get('verifiedShots', 0)}/"
            f"{metrics.get('plannedShots', 0)} clips"
        )
    return 0


def _handle_validate(args: argparse.Namespace) -> int:
    normalized = validate_brief(_brief_from_args(args))
    print(json.dumps(normalized, indent=2, ensure_ascii=False))
    return 0


def _handle_health(args: argparse.Namespace) -> int:
    report = health_report(args.output)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(f"Status: {report['status']}")
        for name, value in report["checks"].items():
            print(f"  {name}: {'ok' if value else 'missing'}")
        for note in report["notes"]:
            print(f"  Note: {note}")
    return 0 if report["ready"] else 1


def _handle_list(
    args: argparse.Namespace,
    *,
    resumable: bool,
) -> int:
    records = (
        list_resumable_runs(args.output, args.limit)
        if resumable
        else list_runs(args.output, args.limit)
    )
    if args.json:
        print(json.dumps(records, indent=2, ensure_ascii=False))
        return 0
    if not records:
        print(
            "No resumable runs found."
            if resumable
            else "No durable runs found."
        )
        return 0
    for record in records:
        print(
            f"{record.get('runId', '?')} | "
            f"{record.get('status', '?')} | "
            f"{record.get('title') or (record.get('brief') or {}).get('title') or 'Untitled'} | "
            f"{record.get('startedAt', '')}"
        )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            return _handle_run(args)
        if args.command == "resume-video":
            return _handle_resume(args)
        if args.command == "voice-run":
            return _handle_voice_run(args)
        if args.command == "video-status":
            return _handle_video_status(args)
        if args.command == "validate":
            return _handle_validate(args)
        if args.command == "health":
            return _handle_health(args)
        if args.command == "list":
            return _handle_list(args, resumable=False)
        if args.command == "list-resumable":
            return _handle_list(args, resumable=True)
        parser.error(f"Unknown command: {args.command}")
    except (
        BriefValidationError,
        json.JSONDecodeError,
        OSError,
    ) as exc:
        print(f"Input error: {exc}", file=sys.stderr)
        return 2
    except PipelineError as exc:
        suffix = (
            f" Run ID: {exc.run_id}." if exc.run_id else ""
        )
        print(
            f"Pipeline error: {exc}.{suffix}",
            file=sys.stderr,
        )
        return 1
    except VoiceStudioError as exc:
        print(f"Voice Studio error: {exc}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
