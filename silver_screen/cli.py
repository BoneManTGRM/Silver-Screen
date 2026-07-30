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
    run_pipeline,
    validate_brief,
)
from .runtime import list_runs
from .science import FORMATS, GENRES, TONES


def _load_json(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise BriefValidationError("brief file must contain a JSON object")
    return payload


def _add_brief_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--brief", help="Path to a JSON production brief")
    parser.add_argument("--premise", help="Story premise")
    parser.add_argument("--title", help="Optional title")
    parser.add_argument("--genre", choices=sorted(GENRES), help="Story genre")
    parser.add_argument("--tone", choices=sorted(TONES), help="Story tone")
    parser.add_argument("--format", dest="fmt", choices=sorted(FORMATS), help="Production format")
    parser.add_argument("--seed", type=int, help="Deterministic generation seed")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="silver-screen",
        description="Deterministic screenplay production with bounded TGRM repair.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Execute a production run")
    _add_brief_arguments(run_parser)
    run_parser.add_argument("--image", action="append", default=[], help="Portrait image path; repeatable")
    run_parser.add_argument("--voice", action="append", default=[], help="Voice file path to inventory; repeatable")
    run_parser.add_argument("--output", help="Run workspace root")
    run_parser.add_argument(
        "--media",
        choices=["off", "cards", "chapters", "hero"],
        default="cards",
        help="Media output level",
    )
    run_parser.add_argument("--max-chapters", type=int, default=4)
    run_parser.add_argument("--max-cycles", type=int)
    run_parser.add_argument("--energy-budget", type=int)
    run_parser.add_argument("--no-persist", action="store_true")
    run_parser.add_argument("--json", action="store_true", help="Print a machine-readable run summary")

    validate_parser = subparsers.add_parser("validate", help="Validate and normalize a brief")
    _add_brief_arguments(validate_parser)

    health_parser = subparsers.add_parser("health", help="Inspect runtime capabilities")
    health_parser.add_argument("--output", help="Run workspace root")
    health_parser.add_argument("--json", action="store_true")

    list_parser = subparsers.add_parser("list", help="List recent durable runs")
    list_parser.add_argument("--output", help="Run workspace root")
    list_parser.add_argument("--limit", type=int, default=20)
    list_parser.add_argument("--json", action="store_true")
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


def _progress(stage: str, percent: int, message: str) -> None:
    print(f"[{percent:3d}%] {stage}: {message}", file=sys.stderr)


def _run_summary(result: dict[str, Any]) -> dict[str, Any]:
    state = result.get("state") or {}
    return {
        "status": result.get("status"),
        "runId": (result.get("run") or {}).get("id"),
        "workspace": (result.get("run") or {}).get("workspace"),
        "title": state.get("title"),
        "format": state.get("format"),
        "sceneCount": len(state.get("scenes") or []),
        "metrics": result.get("metrics") or {},
        "msil": result.get("msil") or {},
        "warnings": result.get("warnings") or [],
        "artifacts": result.get("artifacts") or {},
        "timings": result.get("timings") or {},
    }


def _handle_run(args: argparse.Namespace) -> int:
    brief = _brief_from_args(args)
    result = run_pipeline(
        brief,
        images=args.image,
        voices=args.voice,
        output_root=args.output,
        persist=not args.no_persist,
        render_media=args.media != "off",
        video_mode="cards" if args.media == "off" else args.media,
        max_chapters=args.max_chapters,
        max_cycles=args.max_cycles,
        energy_budget=args.energy_budget,
        progress=None if args.json else _progress,
    )
    summary = _run_summary(result)
    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print(f"Completed: {summary['title']} [{summary['runId']}]")
        print(f"MSIL: {(summary['msil'].get('verdict') or 'unknown').upper()}")
        if summary["workspace"]:
            print(f"Workspace: {summary['workspace']}")
        bundle = summary["artifacts"].get("bundle")
        if bundle:
            print(f"Bundle: {bundle}")
        for warning in summary["warnings"]:
            print(f"Warning: {warning}", file=sys.stderr)
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


def _handle_list(args: argparse.Namespace) -> int:
    records = list_runs(args.output, args.limit)
    if args.json:
        print(json.dumps(records, indent=2, ensure_ascii=False))
        return 0
    if not records:
        print("No durable runs found.")
        return 0
    for record in records:
        print(
            f"{record.get('runId', '?')} | {record.get('status', '?')} | "
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
        if args.command == "validate":
            return _handle_validate(args)
        if args.command == "health":
            return _handle_health(args)
        if args.command == "list":
            return _handle_list(args)
        parser.error(f"Unknown command: {args.command}")
    except (BriefValidationError, json.JSONDecodeError, OSError) as exc:
        print(f"Input error: {exc}", file=sys.stderr)
        return 2
    except PipelineError as exc:
        suffix = f" Run ID: {exc.run_id}." if exc.run_id else ""
        print(f"Pipeline error: {exc}.{suffix}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
