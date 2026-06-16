# Qwoted Slack Bot — Python

Python implementation of the Qwoted Slack bot using [Slack Bolt for Python](https://slack.dev/bolt-python/) and Flask.

Runs **alongside** the TypeScript/Next.js app and shares the same MongoDB database. Both services read and write the same `users`, `posts`, `sessions`, and `action_logs` collections.

---

## Project layout

```
quoted-slack-bot-py/
├── api/
│   └── index.py       ← Vercel entry point — Flask + Bolt wired together
├── bot/
│   ├── config.py      ← reads env vars
│   ├── store.py       ← all MongoDB operations (pymongo)
│   ├── matching.py    ← keyword scoring/matching logic
│   └── handlers.py    ← Bolt slash command, button, and modal handlers
├── requirements.txt
├── vercel.json
└── .env.example
```

---

## How it maps to the TypeScript app

| TypeScript file | Python equivalent |
|---|---|
| `src/config.ts` | `bot/config.py` |
| `src/auth-store.ts` | `bot/store.py` |
| `src/demo-data.ts` | `bot/matching.py` |
| `src/slack.ts` (command + interaction handlers) | `bot/handlers.py` |
| `src/slack.ts` (API route dispatch) | `api/index.py` (Flask routes) |

The seed posts and MongoDB schema are identical so data created by either service is readable by the other.

---

## Local development

```bash
# 1. Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Copy and fill in env vars
cp .env.example .env

# 4. Start the Flask dev server
python -c "
from dotenv import load_dotenv; load_dotenv()
from api.index import app
app.run(port=3001, debug=True)
"
```

The bot listens on port 3001 by default (3000 is used by the TypeScript app).

To receive Slack webhooks locally, use [ngrok](https://ngrok.com):

```bash
ngrok http 3001
```

Then set the following in the Slack app configuration:
- **Slash Commands** → Request URL: `https://<ngrok>.ngrok.io/api/slack/commands`
- **Interactivity & Shortcuts** → Request URL: `https://<ngrok>.ngrok.io/api/slack/interactions`

---

## Deploy to Vercel

```bash
# Install Vercel CLI if needed
npm i -g vercel

# Deploy
vercel

# Set environment variables
vercel env add SLACK_BOT_TOKEN
vercel env add SLACK_SIGNING_SECRET
vercel env add MONGODB_URI
vercel env add APP_BASE_URL      # set to the deployed URL after first deploy
vercel env add DEMO_REQUEST_BASE_URL
```

After deploying, update the Slack app's URLs to point at the Vercel deployment.

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `SLACK_BOT_TOKEN` | Yes | Bot OAuth token (`xoxb-…`) |
| `SLACK_SIGNING_SECRET` | Yes | Used by Bolt to verify Slack requests |
| `MONGODB_URI` | Yes | Same connection string as the TypeScript app |
| `APP_BASE_URL` | Yes | Public URL of this deployment (used to build the `/connect` link) |
| `DEMO_REQUEST_BASE_URL` | No | Base for request links in Slack messages (defaults to `https://demo.qwoted.com/request`) |
| `SESSION_COOKIE_NAME` | No | Cookie name (defaults to `qwoted_session` — must match TypeScript app) |

---

## API endpoints

All endpoints are identical to the TypeScript app so Slack only needs to point at one deployment at a time.

| Endpoint | Method | Description |
|---|---|---|
| `/api/health` | GET | Health check |
| `/api/slack/commands` | POST | Slack slash command webhook |
| `/api/slack/interactions` | POST | Slack button / modal webhook |
| `/api/catalog` | GET | Users + posts from MongoDB |
| `/api/users` | GET | Registered users |
| `/api/posts` | GET / POST | Posts catalog |
| `/api/logs` | GET | Audit log (pass `?limit=N`) |
| `/api/me` | GET | Current session user |
| `/api/auth/register` | POST | Create account |
| `/api/auth/login` | POST | Sign in |
| `/api/auth/logout` | POST | Sign out |
| `/api/link-slack` | POST | Link Slack identity to account |
| `/api/demo-notification` | POST | Test matching without hitting Slack |
