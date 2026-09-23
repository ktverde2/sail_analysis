# sail_analysis

Sailing race analysis for Mojo (Etchells). Two parts:

1. **`skill/sailing-coach/`: the main tool.** The Claude skill that writes race debriefs. Its
   `scripts/analyze.py` turns Njord race data into a report folder (tables, plots, start/leg/maneuver
   numbers) that the skill coaches from. Everything runs inside Claude: no hosting, no login.
2. **`src/sail_analysis/web/`: a private web app** (Google sign-in, upload a GPX/CSV, see a summary).
   Parked for now; kept for when the tool goes to other sailors.

## Run the report locally

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python skill/sailing-coach/scripts/analyze.py \
  samples/njord/2026-07-19_july-odw/race1.csv samples/njord/2026-07-19_july-odw/race2.csv \
  --out /tmp/report
```

Add `--html` for a single self-contained `report.html` (and `--debrief debrief.md` to put a written
debrief at the top); `reports/2026-07-19_july-odw/report.html` is an example.

Open `/tmp/report/event.md`, then `/tmp/report/race1/report.md` and its PNGs. Add `--tws 9` to compare
upwind sailing to the Etchells target card when the logged wind isn't trustworthy (see the sample README).

## Updating the skill in Claude

The skill's source of truth is `skill/sailing-coach/` in this repo. After changing it:

```bash
cd skill && zip -r sailing-coach.zip sailing-coach -x '*/__pycache__/*'
```

Upload `sailing-coach.zip` in Claude's Skills settings, replacing the old version.

## Layout

```
skill/sailing-coach/      the Claude skill: SKILL.md, references, scripts/analyze.py
samples/                  real Njord race exports used as test data
reports/                  generated debriefs (HTML + the numbers behind them)
src/sail_analysis/core/   parsing and metrics (plain Python, no web, no AI)
src/sail_analysis/web/    FastAPI app: Google sign-in, upload page, results page
tests/                    pytest
Dockerfile, fly.toml      deployment to Fly.io
.github/workflows/ci.yml  tests on every PR; deploy on merge to main
```

## Web app

### Run locally

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
cp .env.example .env    # set ALLOWED_EMAILS and DEV_USER_EMAIL to your email
set -a; source .env; set +a
.venv/bin/uvicorn sail_analysis.web.main:app --reload
```

Open http://localhost:8000. With `APP_ENV=dev`, "Sign in" logs you in as `DEV_USER_EMAIL` without
Google. Dev mode can't be turned on in production: the deployed app sets `APP_ENV=prod`.

Tests: `.venv/bin/pytest`

### First deploy (one-time setup)

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
   secret named `FLY_API_TOKEN`. After that, every merge to `main` deploys automatically. Until the
   secret exists, CI skips the deploy step.

### Making changes

Work on a branch, open a PR, and CI runs the tests. Merge to `main` to deploy. To roll back, run
`fly releases` and `fly deploy --image <previous image>`.
