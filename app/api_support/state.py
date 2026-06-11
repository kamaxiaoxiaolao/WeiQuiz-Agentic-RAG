"""Application state access for route modules and background jobs."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

_bound_app: FastAPI | None = None


def bind_app_state(app: FastAPI) -> None:
    global _bound_app
    _bound_app = app


def get_app_state() -> Any:
    if _bound_app is None:
        raise RuntimeError("FastAPI application state has not been bound")
    return _bound_app.state
