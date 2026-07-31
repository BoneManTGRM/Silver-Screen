"""Install small cross-cutting Autonomous Director safeguards."""

from __future__ import annotations

from typing import Any


def install_autonomous_director() -> None:
    from . import autonomous_director, pipeline

    if getattr(pipeline, "_autonomous_director_installed", False):
        return

    original_weakest = autonomous_director._weakest_candidate

    def weakest_candidate(
        queue: dict[str, Any], config: dict[str, Any]
    ) -> dict[str, Any] | None:
        locked_ids = {
            str(item.get("id") or "")
            for item in queue.get("shots") or []
            if isinstance(item, dict) and item.get("timelineLocked")
        }
        if not locked_ids:
            return original_weakest(queue, config)
        filtered = {
            **queue,
            "shots": [
                item
                for item in queue.get("shots") or []
                if not isinstance(item, dict)
                or str(item.get("id") or "") not in locked_ids
            ],
        }
        return original_weakest(filtered, config)

    autonomous_director._weakest_candidate = weakest_candidate
    pipeline._autonomous_director_installed = True


__all__ = ["install_autonomous_director"]
