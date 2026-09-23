"""ASGI entrypoint: uvicorn sail_analysis.web.main:app"""

from .app import create_app

app = create_app()
