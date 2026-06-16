"""
Vercel entry point.

This file is the single serverless function that Vercel invokes for every
request. It wires together:

  - Slack Bolt (handles /api/slack/commands and /api/slack/interactions)
  - Flask (handles all other REST API routes)

Vercel looks for the WSGI `app` variable in this file.
"""

import sys
import os

# Ensure the project root is on sys.path so `bot.*` imports resolve.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from flask import Flask, jsonify, request as flask_request, make_response, redirect, render_template_string
from slack_bolt import App as BoltApp
from slack_bolt.adapter.flask import SlackRequestHandler

from bot import store, matching
from bot.config import get_config
from bot.handlers import register_handlers

# ---------------------------------------------------------------------------
# Bolt app (handles Slack signature verification and event routing)
# ---------------------------------------------------------------------------

config = get_config()

bolt_app = BoltApp(
    token=config.slack_bot_token or None,
    signing_secret=config.slack_signing_secret or None,
)
register_handlers(bolt_app)
slack_handler = SlackRequestHandler(bolt_app)

# ---------------------------------------------------------------------------
# Flask app (handles all HTTP routes)
# ---------------------------------------------------------------------------

app = Flask(__name__)


def _parse_cookies(cookie_header: str) -> dict[str, str]:
    cookies = {}
    if not cookie_header:
        return cookies
    for part in cookie_header.split(";"):
        if "=" in part:
            key, _, value = part.strip().partition("=")
            cookies[key] = value
    return cookies


def _set_session_cookie(response, token: str, secure: bool) -> None:
    response.set_cookie(
        config.session_cookie_name,
        token,
        max_age=60 * 60 * 24 * 7,
        httponly=True,
        secure=secure,
        samesite="Lax",
        path="/",
    )


def _clear_session_cookie(response) -> None:
    response.set_cookie(
        config.session_cookie_name,
        "",
        max_age=0,
        httponly=True,
        samesite="Lax",
        path="/",
    )


def _is_secure() -> bool:
    return (
        flask_request.headers.get("X-Forwarded-Proto") == "https"
        or config.app_base_url.startswith("https://")
    )


def _current_user():
    cookies = _parse_cookies(flask_request.headers.get("Cookie", ""))
    token = cookies.get(config.session_cookie_name)
    return store.find_user_by_session(token)


# ---------------------------------------------------------------------------
# Slack webhook routes — forwarded directly to the Bolt handler
# ---------------------------------------------------------------------------

@app.route("/api/slack/commands", methods=["POST"])
def slack_commands():
    return slack_handler.handle(flask_request)


@app.route("/api/slack/interactions", methods=["POST"])
def slack_interactions():
    return slack_handler.handle(flask_request)


# ---------------------------------------------------------------------------
# Health & catalog
# ---------------------------------------------------------------------------

@app.route("/")
def root():
    return redirect("/api", 302)


@app.route("/api/health")
def health():
    return jsonify({"ok": True})


@app.route("/api")
def api_root():
    return jsonify(
        {
            "name": "Qwoted Request Center (Python)",
            "endpoints": {
                "health": "/api/health",
                "users": "/api/users",
                "posts": "/api/posts",
                "catalog": "/api/catalog",
                "mockData": "/api/mock-data",
                "slackCommands": "/api/slack/commands",
                "slackInteractions": "/api/slack/interactions",
                "demoNotification": "/api/demo-notification",
                "authRegister": "/api/auth/register",
                "authLogin": "/api/auth/login",
                "authLogout": "/api/auth/logout",
                "linkSlack": "/api/link-slack",
                "me": "/api/me",
                "logs": "/api/logs",
            },
        }
    )


@app.route("/api/users")
def users():
    return jsonify({"users": store.list_users()})


@app.route("/api/posts", methods=["GET"])
def get_posts():
    return jsonify({"posts": store.list_posts()})


@app.route("/api/posts", methods=["POST"])
def create_post():
    user = _current_user()
    if not user:
        return redirect("/auth?next=/posts&error=Please+sign+in+first.", 303)

    form = flask_request.form
    try:
        post = store.create_post(
            owner_user_id=user["id"],
            title=form.get("title", ""),
            summary=form.get("summary", ""),
            mode="products" if form.get("mode") == "products" else "experts",
            requested_by=user["name"],
            deadline=form.get("deadline", ""),
            category=form.get("category", ""),
            status=form.get("status", "open"),
        )
        store.append_action_log(
            action="posts.create",
            source="web",
            summary="Created a request post.",
            actor_user_id=user["id"],
            actor_email=user["email"],
            details={"postId": post["id"], "title": post["title"], "mode": post["mode"]},
        )
        return redirect("/posts?success=Post+created.", 303)
    except ValueError as exc:
        store.append_action_log(
            action="posts.create",
            source="web",
            summary="Failed to create a request post.",
            status="error",
            actor_user_id=user["id"],
            actor_email=user["email"],
            details={"error": str(exc)},
        )
        return redirect(f"/posts?error={str(exc)}", 303)


@app.route("/api/catalog")
@app.route("/api/mock-data")
def catalog():
    return jsonify({"users": store.list_users(), "posts": store.list_posts()})


@app.route("/api/logs")
def logs():
    try:
        limit = int(flask_request.args.get("limit", 50))
    except ValueError:
        limit = 50
    return jsonify({"logs": store.list_action_logs(limit=limit)})


@app.route("/api/me")
def me():
    return jsonify({"user": _current_user()})


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@app.route("/api/auth/register", methods=["POST"])
def register():
    form = flask_request.form
    next_url = form.get("next", "/connect")
    try:
        user = store.create_user(
            name=form.get("name", ""),
            email=form.get("email", ""),
            password=form.get("password", ""),
        )
        store.append_action_log(
            action="auth.register",
            source="web",
            summary="Created a new Qwoted account.",
            actor_user_id=user["id"],
            actor_email=user["email"],
            details={"next": next_url},
        )
        token = store.create_session(user["id"])
        resp = make_response(redirect(next_url, 303))
        _set_session_cookie(resp, token, _is_secure())
        return resp
    except ValueError as exc:
        return redirect(f"/auth?error={str(exc)}", 303)


@app.route("/api/auth/login", methods=["POST"])
def login():
    form = flask_request.form
    next_url = form.get("next", "/connect")
    user = store.authenticate_user(form.get("email", ""), form.get("password", ""))
    if not user:
        store.append_action_log(
            action="auth.login",
            source="web",
            summary="Login failed.",
            status="error",
            actor_email=form.get("email"),
            details={"reason": "invalid_credentials"},
        )
        return redirect("/auth?error=Invalid+email+or+password.", 303)

    store.append_action_log(
        action="auth.login",
        source="web",
        summary="Signed in to Qwoted account.",
        actor_user_id=user["id"],
        actor_email=user["email"],
        details={"next": next_url},
    )
    token = store.create_session(user["id"])
    resp = make_response(redirect(next_url, 303))
    _set_session_cookie(resp, token, _is_secure())
    return resp


@app.route("/api/auth/logout", methods=["POST"])
def logout():
    cookies = _parse_cookies(flask_request.headers.get("Cookie", ""))
    token = cookies.get(config.session_cookie_name)
    store.delete_session(token)
    store.append_action_log(
        action="auth.logout",
        source="web",
        summary="Signed out of Qwoted account.",
        details={"hadSession": bool(token)},
    )
    resp = make_response(redirect("/auth", 303))
    _clear_session_cookie(resp)
    return resp


# ---------------------------------------------------------------------------
# Slack account linking
# ---------------------------------------------------------------------------

@app.route("/api/link-slack", methods=["POST"])
def link_slack():
    user = _current_user()
    if not user:
        return redirect("/auth?error=Please+sign+in+first.", 303)

    form = flask_request.form
    slack_team_id = form.get("slackTeamId") or form.get("slack_team_id", "")
    slack_user_id = form.get("slackUserId") or form.get("slack_user_id", "")
    next_url = form.get("next") or f"/connect?slack_team_id={slack_team_id}&slack_user_id={slack_user_id}"

    if not slack_team_id or not slack_user_id:
        return redirect("/connect?error=Missing+Slack+identifiers.", 303)

    linked = store.link_slack_account(user["id"], slack_team_id, slack_user_id)
    if not linked:
        store.append_action_log(
            action="slack.link_account",
            source="web",
            summary="Failed to link Slack account.",
            status="error",
            actor_user_id=user["id"],
            actor_email=user["email"],
            slack_team_id=slack_team_id,
            slack_user_id=slack_user_id,
        )
        return redirect("/connect?error=Unable+to+link+the+Slack+account.", 303)

    store.append_action_log(
        action="slack.link_account",
        source="web",
        summary="Linked Slack account to Qwoted account.",
        actor_user_id=user["id"],
        actor_email=user["email"],
        slack_team_id=slack_team_id,
        slack_user_id=slack_user_id,
        details={"next": next_url},
    )
    sep = "&" if "?" in next_url else "?"
    return redirect(f"{next_url}{sep}success=Slack+account+linked.", 303)


# ---------------------------------------------------------------------------
# Demo utility
# ---------------------------------------------------------------------------

@app.route("/api/demo-notification", methods=["POST"])
def demo_notification():
    data = flask_request.get_json(silent=True) or {}
    mode = "products" if data.get("mode") == "products" else "experts"
    posts = store.list_posts()
    copy = matching.build_demo_copy(
        mode=mode,
        title=data.get("title", "Gas prices"),
        description=data.get("description", "Media request workflow"),
        audience=data.get("audience", "Economists or energy experts"),
        deadline=data.get("deadline", "Friday"),
        category=data.get("category", "Newsroom"),
        request_base_url=config.demo_request_base_url,
        posts=posts,
    )
    return jsonify(copy)


# ---------------------------------------------------------------------------
# HTML pages
# ---------------------------------------------------------------------------

_SHARED_HEAD = """\
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #f5f5f5; color: #111; min-height: 100vh;
         display: flex; align-items: center; justify-content: center; padding: 1rem; }
  .card { background: #fff; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,.1);
          padding: 2rem; width: 100%; max-width: 400px; }
  h1 { font-size: 1.25rem; font-weight: 600; margin-bottom: 1.5rem; }
  label { display: block; font-size: .875rem; font-weight: 500; margin-bottom: .25rem; margin-top: 1rem; }
  input[type=text], input[type=email], input[type=password] {
    width: 100%; padding: .5rem .75rem; border: 1px solid #ddd; border-radius: 6px;
    font-size: .9375rem; }
  input:focus { outline: 2px solid #4A90E2; outline-offset: -1px; }
  button[type=submit] { margin-top: 1.25rem; width: 100%; padding: .625rem;
    background: #4A90E2; color: #fff; border: none; border-radius: 6px;
    font-size: .9375rem; font-weight: 500; cursor: pointer; }
  button[type=submit]:hover { background: #357ABD; }
  .alert { padding: .75rem 1rem; border-radius: 6px; font-size: .875rem; margin-bottom: 1rem; }
  .alert-error { background: #FEE2E2; color: #991B1B; }
  .alert-success { background: #D1FAE5; color: #065F46; }
  .divider { border: none; border-top: 1px solid #eee; margin: 1.5rem 0; }
  .link-btn { display: block; text-align: center; margin-top: .75rem;
    font-size: .875rem; color: #4A90E2; text-decoration: none; }
  .link-btn:hover { text-decoration: underline; }
  .tab-toggle { display: flex; gap: .5rem; margin-bottom: 1.5rem; }
  .tab-toggle a { flex: 1; text-align: center; padding: .5rem;
    border: 1px solid #ddd; border-radius: 6px; font-size: .875rem;
    text-decoration: none; color: #555; }
  .tab-toggle a.active { background: #4A90E2; color: #fff; border-color: #4A90E2; font-weight: 500; }
  .slack-badge { display: flex; align-items: center; gap: .5rem;
    background: #f0f4ff; border: 1px solid #c7d7f5; border-radius: 6px;
    padding: .75rem 1rem; margin-bottom: 1.25rem; font-size: .875rem; color: #2c3e6b; }
  .slack-badge svg { flex-shrink: 0; }
</style>"""

_AUTH_PAGE = ("""\
<!doctype html>
<html lang="en">
<head>""" + _SHARED_HEAD + """<title>Sign in — Qwoted</title></head>
<body>
<div class="card">
  <h1>Qwoted</h1>
  {% if error %}<div class="alert alert-error">{{ error }}</div>{% endif %}
  <div class="tab-toggle">
    <a href="/auth?tab=login&next={{ next_url }}" class="{{ 'active' if tab == 'login' else '' }}">Sign in</a>
    <a href="/auth?tab=register&next={{ next_url }}" class="{{ 'active' if tab == 'register' else '' }}">Create account</a>
  </div>

  {% if tab == 'login' %}
  <form method="POST" action="/api/auth/login">
    <input type="hidden" name="next" value="{{ next_url }}">
    <label for="email">Email</label>
    <input type="email" id="email" name="email" required autocomplete="email">
    <label for="password">Password</label>
    <input type="password" id="password" name="password" required autocomplete="current-password">
    <button type="submit">Sign in</button>
  </form>
  <a class="link-btn" href="/auth?tab=register&next={{ next_url }}">No account? Create one →</a>

  {% else %}
  <form method="POST" action="/api/auth/register">
    <input type="hidden" name="next" value="{{ next_url }}">
    <label for="name">Name</label>
    <input type="text" id="name" name="name" required autocomplete="name">
    <label for="email">Email</label>
    <input type="email" id="email" name="email" required autocomplete="email">
    <label for="password">Password</label>
    <input type="password" id="password" name="password" required autocomplete="new-password">
    <button type="submit">Create account</button>
  </form>
  <a class="link-btn" href="/auth?tab=login&next={{ next_url }}">Already have an account? Sign in →</a>
  {% endif %}
</div>
</body>
</html>
""")

_CONNECT_PAGE = ("""\
<!doctype html>
<html lang="en">
<head>""" + _SHARED_HEAD + """<title>Connect Slack — Qwoted</title></head>
<body>
<div class="card">
  <h1>Connect your Slack account</h1>
  {% if error %}<div class="alert alert-error">{{ error }}</div>{% endif %}
  {% if success %}<div class="alert alert-success">{{ success }}</div>{% endif %}

  {% if success %}
    <p style="font-size:.875rem;color:#555;margin-bottom:1rem;">
      Your Slack account is linked. You can close this window and return to Slack.
    </p>
  {% elif not user %}
    <p style="font-size:.875rem;color:#555;margin-bottom:1.25rem;">
      Sign in to your Qwoted account to link it to Slack.
    </p>
    <div class="slack-badge">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14.5 10c-.83 0-1.5-.67-1.5-1.5v-5c0-.83.67-1.5 1.5-1.5s1.5.67 1.5 1.5v5c0 .83-.67 1.5-1.5 1.5z"/><path d="M20.5 10H19V8.5c0-.83.67-1.5 1.5-1.5s1.5.67 1.5 1.5-.67 1.5-1.5 1.5z"/><path d="M9.5 14c.83 0 1.5.67 1.5 1.5v5c0 .83-.67 1.5-1.5 1.5S8 21.33 8 20.5v-5c0-.83.67-1.5 1.5-1.5z"/><path d="M3.5 14H5v1.5c0 .83-.67 1.5-1.5 1.5S2 16.33 2 15.5 2.67 14 3.5 14z"/><path d="M14 14.5c0-.83.67-1.5 1.5-1.5h5c.83 0 1.5.67 1.5 1.5s-.67 1.5-1.5 1.5h-5c-.83 0-1.5-.67-1.5-1.5z"/><path d="M15.5 19H14v1.5c0 .83.67 1.5 1.5 1.5s1.5-.67 1.5-1.5-.67-1.5-1.5-1.5z"/><path d="M10 9.5C10 8.67 9.33 8 8.5 8h-5C2.67 8 2 8.67 2 9.5S2.67 11 3.5 11h5c.83 0 1.5-.67 1.5-1.5z"/><path d="M8.5 5H10V3.5C10 2.67 9.33 2 8.5 2S7 2.67 7 3.5 7.67 5 8.5 5z"/></svg>
      Slack user: {{ slack_user_id }} / Team: {{ slack_team_id }}
    </div>
    <a class="link-btn" href="/auth?next={{ connect_url }}" style="display:block;width:100%;text-align:center;padding:.625rem;background:#4A90E2;color:#fff;border-radius:6px;font-weight:500;text-decoration:none;font-size:.9375rem;">Sign in to Qwoted</a>
    <a class="link-btn" href="/auth?tab=register&next={{ connect_url }}">No account? Create one →</a>
  {% else %}
    <p style="font-size:.875rem;color:#555;margin-bottom:1.25rem;">
      Signed in as <strong>{{ user.email }}</strong>. Click below to link your Slack account.
    </p>
    <div class="slack-badge">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14.5 10c-.83 0-1.5-.67-1.5-1.5v-5c0-.83.67-1.5 1.5-1.5s1.5.67 1.5 1.5v5c0 .83-.67 1.5-1.5 1.5z"/><path d="M20.5 10H19V8.5c0-.83.67-1.5 1.5-1.5s1.5.67 1.5 1.5-.67 1.5-1.5 1.5z"/><path d="M9.5 14c.83 0 1.5.67 1.5 1.5v5c0 .83-.67 1.5-1.5 1.5S8 21.33 8 20.5v-5c0-.83.67-1.5 1.5-1.5z"/><path d="M3.5 14H5v1.5c0 .83-.67 1.5-1.5 1.5S2 16.33 2 15.5 2.67 14 3.5 14z"/><path d="M14 14.5c0-.83.67-1.5 1.5-1.5h5c.83 0 1.5.67 1.5 1.5s-.67 1.5-1.5 1.5h-5c-.83 0-1.5-.67-1.5-1.5z"/><path d="M15.5 19H14v1.5c0 .83.67 1.5 1.5 1.5s1.5-.67 1.5-1.5-.67-1.5-1.5-1.5z"/><path d="M10 9.5C10 8.67 9.33 8 8.5 8h-5C2.67 8 2 8.67 2 9.5S2.67 11 3.5 11h5c.83 0 1.5-.67 1.5-1.5z"/><path d="M8.5 5H10V3.5C10 2.67 9.33 2 8.5 2S7 2.67 7 3.5 7.67 5 8.5 5z"/></svg>
      Slack user: {{ slack_user_id }} / Team: {{ slack_team_id }}
    </div>
    <form method="POST" action="/api/link-slack">
      <input type="hidden" name="slack_team_id" value="{{ slack_team_id }}">
      <input type="hidden" name="slack_user_id" value="{{ slack_user_id }}">
      <input type="hidden" name="next" value="{{ connect_url }}">
      <button type="submit">Link Slack account</button>
    </form>
    <hr class="divider">
    <form method="POST" action="/api/auth/logout" style="margin-top:0">
      <button type="submit" style="width:100%;padding:.5rem;background:#fff;color:#555;border:1px solid #ddd;border-radius:6px;font-size:.875rem;cursor:pointer;">Sign out</button>
    </form>
  {% endif %}
</div>
</body>
</html>
""")


@app.route("/auth")
def auth_page():
    user = _current_user()
    if user:
        next_url = flask_request.args.get("next", "/connect")
        return redirect(next_url, 302)
    tab = flask_request.args.get("tab", "login")
    error = flask_request.args.get("error", "")
    next_url = flask_request.args.get("next", "/connect")
    return render_template_string(_AUTH_PAGE, tab=tab, error=error, next_url=next_url)


@app.route("/connect")
def connect_page():
    slack_team_id = flask_request.args.get("slack_team_id", "")
    slack_user_id = flask_request.args.get("slack_user_id", "")
    error = flask_request.args.get("error", "")
    success = flask_request.args.get("success", "")
    user = _current_user()
    connect_url = (
        f"/connect?slack_team_id={slack_team_id}&slack_user_id={slack_user_id}"
        if slack_team_id and slack_user_id
        else "/connect"
    )
    return render_template_string(
        _CONNECT_PAGE, user=user, slack_team_id=slack_team_id,
        slack_user_id=slack_user_id, connect_url=connect_url,
        error=error, success=success,
    )
