"""Runtime-safe per-shot model routing adapter."""

from __future__ import annotations

from typing import Any


def _route(state: dict[str, Any], shot: dict[str, Any]) -> dict[str, Any]:
    from . import model_routing

    for name in ("route_shot", "recommend_model_for_shot", "model_for_shot"):
        function = getattr(model_routing, name, None)
        if callable(function):
            try:
                result = function(state, shot)
                if isinstance(result, dict):
                    return result
            except TypeError:
                try:
                    result = function(shot, state)
                    if isinstance(result, dict):
                        return result
                except Exception:
                    pass
            except Exception:
                pass
    build_plan = getattr(model_routing, "build_model_routing_plan", None) or getattr(
        model_routing, "build_routing_plan", None
    )
    if callable(build_plan):
        try:
            plan = build_plan(state, [shot])
            routes = (
                plan.get("routes")
                or plan.get("shots")
                or plan.get("recommendations")
                or []
            )
            if routes and isinstance(routes[0], dict):
                return routes[0]
        except Exception:
            pass
    return {}


def install_model_routing_runtime() -> None:
    from . import ai_video, pipeline

    if getattr(pipeline, "_model_routing_runtime_installed", False):
        return

    original_process = ai_video._process_prediction

    def process_prediction(**kwargs: Any) -> None:
        state = kwargs.get("state") or {}
        shot = kwargs.get("shot") or {}
        client = kwargs.get("client")
        route = _route(state, shot)
        if not route:
            route = {
                "category": "general",
                "model": str(getattr(client, "model", "") or ""),
                "reason": "No specialized route was configured; use the active provider model.",
                "fallback": True,
            }
        shot["modelRoute"] = route
        model = str(
            route.get("model")
            or route.get("recommendedModel")
            or route.get("modelId")
            or ""
        ).strip()
        current_model = str(getattr(client, "model", "") or "").strip()
        if client is not None and model and "/" in model and model != current_model:
            try:
                routed_client = client.__class__(
                    token=getattr(client, "token", None),
                    model=model,
                    timeout_seconds=int(
                        getattr(client, "timeout_seconds", 1200) or 1200
                    ),
                    poll_seconds=float(
                        getattr(client, "poll_seconds", 3.0) or 3.0
                    ),
                )
                kwargs["client"] = routed_client
                shot["providerModel"] = model
                shot["modelRoute"]["activated"] = True
            except Exception as exc:
                shot["modelRoute"]["activated"] = False
                shot["modelRoute"]["fallbackReason"] = str(exc)[:800]
                shot["providerModel"] = current_model
        else:
            shot["modelRoute"]["activated"] = bool(model and model == current_model)
            shot["providerModel"] = current_model or model
        original_process(**kwargs)

    ai_video._process_prediction = process_prediction
    pipeline._model_routing_runtime_installed = True


__all__ = ["install_model_routing_runtime"]
