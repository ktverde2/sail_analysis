# sail_analysis

A private, login-protected website for analyzing sailing data. Upload a GPX track or a CSV log
(Vakaros, RaceSense, Njord export) and get distance, speeds, tack/gybe count, a speed chart and a
track plot.

Only Google accounts listed in `ALLOWED_EMAILS` can sign in. Everyone else gets a 403.

## Layout

```
src/sail_analysis/core/   parsing and metrics (plain Python, no web, no AI)
src/sail_analysis/web/    FastAPI app: Google sign-in, upload page, results page
tests/                    pytest
Dockerfile, fly.toml      deployment to Fly.io
.github/workflows/ci.yml  tests on every PR; deploy on merge to main
```

## Run locally

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
cp .env.example .env    # set ALLOWED_EMAILS and DEV_USER_EMAIL to your email
set -a; source .env; set +a
.venv/bin/uvicorn sail_analysis.web.main:app --reload
```

Open http://localhost:8000. With `APP_ENV=dev`, "Sign in" logs you in as `DEV_USER_EMAIL` without
Google. Dev mode can't be turned on in production: the deployed app sets `APP_ENV=prod`.

Tests: `.venv/bin/pytest`

## First deploy (one-time setup)

1. **Google sign-in.** In [Google Cloud Console](https://console.cloud.google.com/apis/credentials),
   create an OAuth client ID (type: Web application). Add the authorized redirect URI
   `https://<your-app>.fly.dev/auth/callback`. Keep the client ID and secret.
2. **Fly.io.** Install `flyctl`, run `fly auth login`, change `app` in `fly.toml` to a unique name,
   then `fly launch --no-deploy --copy-config`.
3. **Secrets** (stored by Fly, never in git):
   ```bash
   fly secrets set \
     SECRET_KEY=$(python3 -c "import secrets;print(secrets.token_urlsafe(48))") \
     ALLOWED_EMAILS=you@gmail.com \
     GOOGLE_CLIENT_ID=... GOOGLE_CLIENT_SECRET=...
   ```
4. `fly deploy`
5. **Automatic deploys.** Run `fly tokens create deploy` and add the result as a GitHub Actions
   secret named `FLY_API_TOKEN`. After that, every merge to `main` deploys automatically.

## Making changes

Work on a branch, open a PR, and CI runs the tests. Merge to `main` to deploy. To roll back, run
`fly releases` and `fly deploy --image <previous image>`.
