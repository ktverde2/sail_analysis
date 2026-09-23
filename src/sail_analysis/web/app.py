"""FastAPI app: login-gated pages for uploading and analyzing sailing logs."""

from __future__ import annotations

from pathlib import Path

import plotly
from fastapi import Depends, FastAPI, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from ..core import parse_file, summarize
from ..core.parsers import ParseError
from .auth import LoginRequired, build_router, current_user
from .settings import Settings, load_settings

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
PLOTLY_JS = Path(plotly.__file__).parent / "package_data" / "plotly.min.js"
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key or "dev-only-insecure-key",
        https_only=not settings.is_dev,
        same_site="lax",
        max_age=14 * 24 * 3600,
    )
    app.include_router(build_router(settings))

    @app.exception_handler(LoginRequired)
    async def _to_login(request: Request, exc: LoginRequired):
        return RedirectResponse("/login", status_code=303)

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        return response

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    @app.get("/static/plotly.min.js")
    async def plotly_js():
        return FileResponse(PLOTLY_JS, headers={"Cache-Control": "public, max-age=604800"})

    @app.get("/login", response_class=HTMLResponse)
    async def login(request: Request):
        return templates.TemplateResponse(request, "login.html", {"dev": settings.is_dev})

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request, user: str = Depends(current_user)):
        return templates.TemplateResponse(request, "upload.html", {"user": user})

    @app.post("/analyze", response_class=HTMLResponse)
    async def analyze(request: Request, file: UploadFile, user: str = Depends(current_user)):
        data = await file.read(MAX_UPLOAD_BYTES + 1)
        error = None
        summary = None
        if len(data) > MAX_UPLOAD_BYTES:
            error = "File is larger than 50 MB"
        else:
            try:
                summary = summarize(parse_file(file.filename or "", data))
            except ParseError as e:
                error = str(e)
        if error:
            return templates.TemplateResponse(
                request, "upload.html", {"user": user, "error": error}, status_code=400
            )
        return templates.TemplateResponse(
            request, "result.html", {"user": user, "s": summary, "filename": file.filename}
        )

    return app
