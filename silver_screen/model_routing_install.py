"""Install per-shot model routing without changing the public video API."""

from __future__ import annotations

from typing import Any

from .model_routing import route_shot


def install_model_routing() -> None:
    from . import ai_video, pipeline

    if getattr(pipeline, "_model_routing_installed", False):
        return

    original_process = ai_video._process_prediction

    def process_prediction(**kwargs: Any) -> None:
        state = kwargs.get("state") or {}
        shot = kwargs.get("shot") or {}
        client = kwargs.get("client")
        route = route_shot(state, shot)
        shot["modelRoute"] = route
        model = str(route.get("model") or "").strip()
        current_model = str(getattr(client, "model", "") or "").strip()
        if (
            client is not None
            and model
            and "/" in model
            and model != current_model
        ):
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
            shot["modelRoute"]["activated"] = bool(model == current_model)
            shot["providerModel"] = current_model or model
        original_process(**kwargs)

    ai_video._process_prediction = process_prediction
    pipeline._model_routing_installed = True


__all__ = ["install_model_routing"]
