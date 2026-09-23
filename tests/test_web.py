import pytest
from fastapi.testclient import TestClient

from sail_analysis.web.app import create_app
from sail_analysis.web.settings import Settings

ALLOWED = "kevin@example.com"


def settings(**kw) -> Settings:
    base = {
        "app_env": "dev",
        "secret_key": "x" * 40,
        "allowed_emails": frozenset({ALLOWED}),
        "google_client_id": "",
        "google_client_secret": "",
        "dev_user_email": ALLOWED,
    }
    base.update(kw)
    return Settings(**base)


def client(**kw) -> TestClient:
    return TestClient(create_app(settings(**kw)), follow_redirects=False)


def test_pages_redirect_to_login_when_signed_out():
    c = client()
    assert c.get("/").headers["location"] == "/login"
    r = c.post("/analyze", files={"file": ("a.csv", b"x")})
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_dev_sign_in_then_analyze(zigzag_csv):
    c = client()
    assert c.get("/auth/google").status_code == 303
    assert c.get("/").status_code == 200
    r = c.post("/analyze", files={"file": ("zz.csv", zigzag_csv.encode())})
    assert r.status_code == 200
    assert "Tacks + gybes" in r.text
    assert c.post("/logout").status_code == 303
    assert c.get("/").status_code == 303


def test_non_allowlisted_email_rejected():
    c = client(dev_user_email="stranger@example.com")
    assert c.get("/auth/google").status_code == 403
    assert c.get("/").status_code == 303


def test_bad_upload_shows_error():
    c = client()
    c.get("/auth/google")
    r = c.post("/analyze", files={"file": ("a.txt", b"hello")})
    assert r.status_code == 400 and "Unsupported" in r.text


def test_prod_settings_require_secrets(monkeypatch):
    from sail_analysis.web.settings import load_settings

    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("ALLOWED_EMAILS", ALLOWED)
    monkeypatch.setenv("SECRET_KEY", "short")
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        load_settings()
    monkeypatch.setenv("SECRET_KEY", "y" * 40)
    with pytest.raises(RuntimeError, match="GOOGLE_CLIENT_ID"):
        load_settings()
    monkeypatch.delenv("ALLOWED_EMAILS")
    with pytest.raises(RuntimeError, match="ALLOWED_EMAILS"):
        load_settings()


def test_chart_library_served_locally():
    r = client().get("/static/plotly.min.js")
    assert r.status_code == 200 and len(r.content) > 100_000


def test_security_headers():
    r = client().get("/login")
    assert r.headers["X-Frame-Options"] == "DENY"
    assert "noindex" in r.headers["X-Robots-Tag"]
