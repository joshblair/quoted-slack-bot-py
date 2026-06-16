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

from flask import Flask, jsonify, request as flask_request, make_response, redirect
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
