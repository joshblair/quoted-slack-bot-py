"""
Slack Bolt handlers.

Three entry points registered on the Bolt App:
  @app.command("/quoted")              — slash command
  @app.action("quoted_call_experts")  — button click
  @app.action("quoted_call_products") — button click
  @app.view("quoted_request_submit")  — modal submission
"""

import json
import logging

from slack_bolt import App

from bot import store, matching
from bot.config import get_config

logger = logging.getLogger(__name__)


def _build_connect_url(base_url: str, team_id: str, user_id: str) -> str:
    return f"{base_url.rstrip('/')}/connect?slack_team_id={team_id}&slack_user_id={user_id}"


def _menu_blocks() -> list[dict]:
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*Qwoted request menu*\nChoose one of the structured newsroom workflows.",
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Call for Experts"},
                    "action_id": "quoted_call_experts",
                    "value": "experts",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Call for Products"},
                    "action_id": "quoted_call_products",
                    "value": "products",
                },
            ],
        },
    ]


def _connect_blocks(connect_url: str) -> list[dict]:
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*Qwoted request menu*\nYour Slack user is not linked yet.",
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Connect Qwoted Account"},
                    "url": connect_url,
                }
            ],
        },
    ]


def _build_modal(mode: str, team_id: str, user_id: str, channel_id: str | None) -> dict:
    is_experts = mode == "experts"
    title_text = "Call for Experts" if is_experts else "Call for Products"
    target_label = "Who are you looking for?" if is_experts else "What product are you looking for?"
    private_metadata = json.dumps(
        {"mode": mode, "teamId": team_id, "userId": user_id, "channelId": channel_id}
    )
    return {
        "type": "modal",
        "callback_id": "quoted_request_submit",
        "title": {"type": "plain_text", "text": title_text},
        "submit": {"type": "plain_text", "text": "Submit"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "private_metadata": private_metadata,
        "blocks": [
            {
                "type": "input",
                "block_id": "title",
                "label": {"type": "plain_text", "text": "Title / Topic"},
                "element": {"type": "plain_text_input", "action_id": "value"},
            },
            {
                "type": "input",
                "block_id": "description",
                "label": {"type": "plain_text", "text": "Description"},
                "optional": True,
                "element": {
                    "type": "plain_text_input",
                    "action_id": "value",
                    "multiline": True,
                },
            },
            {
                "type": "input",
                "block_id": "audience",
                "label": {"type": "plain_text", "text": target_label},
                "optional": True,
                "element": {"type": "plain_text_input", "action_id": "value"},
            },
            {
                "type": "input",
                "block_id": "deadline",
                "label": {"type": "plain_text", "text": "Deadline"},
                "optional": True,
                "element": {
                    "type": "plain_text_input",
                    "action_id": "value",
                    "placeholder": {"type": "plain_text", "text": "Friday"},
                },
            },
            {
                "type": "input",
                "block_id": "category",
                "label": {"type": "plain_text", "text": "Category"},
                "optional": True,
                "element": {"type": "plain_text_input", "action_id": "value"},
            },
        ],
    }


def _extract_value(values: dict, block_id: str) -> str:
    block = values.get(block_id, {})
    if not block:
        return ""
    first = next(iter(block.values()), {})
    if "selected_option" in first and first["selected_option"]:
        return first["selected_option"]["value"]
    return first.get("value") or ""


def _post_submission_to_channel(client, channel_id: str, copy: dict, mode: str, title: str, deadline: str, category: str, description: str, audience: str) -> None:
    request_label = "Call for Experts" if mode == "experts" else "Call for Products"
    matched_post = copy.get("matchedPost")
    candidate_title = matched_post["title"] if matched_post else "No live candidate yet"
    candidate_summary = (
        matched_post["summary"]
        if matched_post
        else "Create posts in the Posts section to populate the live catalog."
    )
    candidate_score = str(copy.get("matchedPostScore", "—")) if matched_post else "—"
    request_summary = description or audience or "No additional details provided."

    client.chat_postMessage(
        channel=channel_id,
        text=f"{request_label} submitted: {title}",
        blocks=[
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "Request received"},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{request_label} received* for *{title or 'Untitled request'}*.",
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Matched candidate*\n{candidate_title}"},
                    {"type": "mrkdwn", "text": f"*Score*\n{candidate_score}"},
                    {"type": "mrkdwn", "text": f"*Deadline*\n{deadline or 'Not provided'}"},
                    {"type": "mrkdwn", "text": f"*Category*\n{category or 'Not provided'}"},
                ],
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Candidate summary*\n{candidate_summary}\n\n*Request details*\n{request_summary}",
                },
            },
        ],
    )


def register_handlers(app: App) -> None:
    config = get_config()

    # ------------------------------------------------------------------
    # /quoted slash command
    # ------------------------------------------------------------------
    @app.command("/quoted")
    def handle_slash_command(ack, command, respond):
        ack()
        team_id = command.get("team_id", "")
        user_id = command.get("user_id", "")

        linked = store.find_linked_user(team_id, user_id)
        store.append_action_log(
            action="slack.command",
            source="slack",
            summary="Slack slash command opened the workflow menu." if linked else "Slack slash command showed connect prompt.",
            slack_team_id=team_id,
            slack_user_id=user_id,
            details={"command": command.get("command"), "linked": bool(linked)},
        )

        if linked:
            respond(blocks=_menu_blocks(), text="Choose Call for Experts or Call for Products.")
        else:
            connect_url = _build_connect_url(config.app_base_url, team_id, user_id)
            respond(blocks=_connect_blocks(connect_url), text="Connect your Qwoted account to continue.")

    # ------------------------------------------------------------------
    # Button: Call for Experts
    # ------------------------------------------------------------------
    @app.action("quoted_call_experts")
    def handle_experts_button(ack, body, client):
        ack()
        _open_modal(body, client, "experts")

    # ------------------------------------------------------------------
    # Button: Call for Products
    # ------------------------------------------------------------------
    @app.action("quoted_call_products")
    def handle_products_button(ack, body, client):
        ack()
        _open_modal(body, client, "products")

    def _open_modal(body: dict, client, mode: str) -> None:
        team_id = body.get("team", {}).get("id", "")
        user_id = body.get("user", {}).get("id", "")
        trigger_id = body.get("trigger_id", "")
        channel_id = body.get("container", {}).get("channel_id")

        # Call views_open immediately — trigger_id expires in 3 seconds.
        # Log after so MongoDB latency doesn't consume the window.
        try:
            client.views_open(trigger_id=trigger_id, view=_build_modal(mode, team_id, user_id, channel_id))
            store.append_action_log(
                action="slack.button_click",
                source="slack",
                summary=f"Opened {'Call for Experts' if mode == 'experts' else 'Call for Products'} modal.",
                slack_team_id=team_id,
                slack_user_id=user_id,
                details={"mode": mode},
            )
        except Exception as exc:
            logger.error("Failed to open modal: %s", exc)
            store.append_action_log(
                action="slack.button_click",
                source="slack",
                summary="Failed to open modal.",
                status="error",
                slack_team_id=team_id,
                slack_user_id=user_id,
                details={"error": str(exc)},
            )

    # ------------------------------------------------------------------
    # Modal submission
    # ------------------------------------------------------------------
    @app.view("quoted_request_submit")
    def handle_modal_submit(ack, body, client, view):
        ack()

        metadata = json.loads(view.get("private_metadata", "{}"))
        mode = metadata.get("mode", "experts")
        team_id = metadata.get("teamId", "")
        user_id = metadata.get("userId", "")
        channel_id = metadata.get("channelId")

        values = view.get("state", {}).get("values", {})
        title = _extract_value(values, "title")
        description = _extract_value(values, "description")
        audience = _extract_value(values, "audience")
        deadline = _extract_value(values, "deadline")
        category = _extract_value(values, "category")

        posts = store.list_posts()
        copy = matching.build_demo_copy(
            mode=mode,
            title=title,
            description=description,
            audience=audience,
            deadline=deadline,
            category=category,
            request_base_url=config.demo_request_base_url,
            posts=posts,
        )

        matched_post = copy.get("matchedPost")
        store.append_action_log(
            action="slack.modal_submit",
            source="slack",
            summary="Slack modal submission received and acknowledged.",
            slack_team_id=team_id,
            slack_user_id=user_id,
            details={
                "mode": mode,
                "title": title,
                "requestId": copy["requestId"],
                "requestUrl": copy["requestUrl"],
                "matchedPost": {"id": matched_post.get("id"), "title": matched_post.get("title"), "score": copy["matchedPostScore"]} if matched_post else None,
            },
        )

        if channel_id:
            try:
                _post_submission_to_channel(client, channel_id, copy, mode, title, deadline, category, description, audience)
                store.append_action_log(
                    action="slack.reply_delivery",
                    source="slack",
                    summary="Posted submission confirmation to Slack.",
                    slack_team_id=team_id,
                    slack_user_id=user_id,
                    details={"channelId": channel_id, "requestId": copy["requestId"]},
                )
            except Exception as exc:
                logger.error("Failed to post channel message: %s", exc)
                store.append_action_log(
                    action="slack.reply_delivery",
                    source="slack",
                    summary="Failed to post submission confirmation to Slack.",
                    status="error",
                    slack_team_id=team_id,
                    slack_user_id=user_id,
                    details={"channelId": channel_id, "error": str(exc)},
                )
