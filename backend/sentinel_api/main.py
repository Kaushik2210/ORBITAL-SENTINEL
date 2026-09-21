"""ASGI entry point: ``uvicorn sentinel_api.main:app``."""

from .app import create_app

app = create_app()
