"""Shared Claude settings."""

from __future__ import annotations

MODEL = "claude-opus-5-5"


def fallback_kwargs() -> dict:
    """Server-side refusal fallback: if a request is declined, the API retries it on a suitable model."""
    return {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}


def check_stop(response, what: str) -> None:
    if response.stop_reason == "refusal":
        detail = response.stop_details.explanation if getattr(response, "stop_details", None) else ""
        raise RuntimeError(f"Claude declined {what}: {detail}")
    if response.stop_reason == "max_tokens":
        raise RuntimeError(f"Claude ran out of output space while {what}")
