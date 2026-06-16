"""
Keyword matching between a Slack form submission and the posts catalog.

Mirrors the scoring logic in the TypeScript demo-data.ts so results are
consistent regardless of which service handles the request.
"""

import re
import secrets
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class MatchResult:
    search_text: str
    search_tokens: list[str]
    matched_post: Optional[dict]
    matched_post_score: int
    all_scores: list[dict] = field(default_factory=list)


def _normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return text.strip()


def _tokenize(text: str) -> list[str]:
    return [t for t in _normalize(text).split() if len(t) > 1]


def _score_post(post: dict, tokens: list[str], mode: str) -> int:
    haystack = _normalize(
        " ".join(
            filter(
                None,
                [
                    post.get("title", ""),
                    post.get("summary", ""),
                    post.get("requestedBy", ""),
                    post.get("deadline", ""),
                    post.get("category", ""),
                    post.get("status", ""),
                    post.get("mode", ""),
                    mode,
                ],
            )
        )
    )

    if not tokens:
        return 10 if post.get("mode") == mode else 1

    score = 5 if post.get("mode") == mode else 0
    for token in tokens:
        if token in haystack:
            score += 4 if len(token) > 3 else 2

    if _normalize(post.get("title", "")).find(" ".join(tokens)) != -1:
        score += 8

    return score


def analyze_request_match(mode: str, title: str, description: str, audience: str, category: str, deadline: str, posts: list[dict]) -> MatchResult:
    search_text = " ".join(filter(None, [title, description, audience, category, deadline]))
    tokens = _tokenize(search_text)

    ranked = sorted(
        [{"post": p, "score": _score_post(p, tokens, mode)} for p in posts],
        key=lambda x: x["score"],
        reverse=True,
    )

    best = next(
        (r for r in ranked if r["score"] > 0 and r["post"].get("mode") == mode),
        ranked[0] if ranked else None,
    )

    return MatchResult(
        search_text=search_text,
        search_tokens=tokens,
        matched_post=best["post"] if best else None,
        matched_post_score=best["score"] if best else 0,
        all_scores=[
            {
                "postId": r["post"].get("id", ""),
                "title": r["post"].get("title", ""),
                "score": r["score"],
                "mode": r["post"].get("mode", ""),
            }
            for r in ranked
        ],
    )


def make_request_id() -> str:
    return f"demo-{secrets.token_hex(4)}"


def build_demo_copy(
    mode: str,
    title: str,
    description: str,
    audience: str,
    deadline: str,
    category: str,
    request_base_url: str,
    posts: list[dict],
    requestor_name: str = "Qwoted user",
) -> dict:
    request_id = make_request_id()
    request_url = f"{request_base_url.rstrip('/')}/{request_id}"
    match = analyze_request_match(mode, title, description, audience, category, deadline, posts)

    request_label = "Call for Experts" if mode == "experts" else "Call for Products"
    looking_for_label = "Looking for" if mode == "experts" else "What product are you looking for?"
    summary_text = (audience or description).strip()

    matched_post = match.matched_post
    candidate_title = matched_post["title"] if matched_post else "No live candidate yet"
    candidate_summary = (
        matched_post["summary"]
        if matched_post
        else "Create posts in the Posts section to populate the live catalog."
    )

    confirmation_lines = [
        f"OK. Your {request_label} request has been submitted.",
        "",
        f"Topic: {title}",
        f"{looking_for_label}: {summary_text or 'Not provided'}",
        f"Deadline: {deadline or 'Not provided'}",
        f"Category: {category or 'Not provided'}",
        "",
        f"Requested by: {requestor_name}",
        f"View request: {request_url}",
    ]

    if matched_post and match.matched_post_score > 0:
        confirmation_lines += ["", f"Matched candidate: {candidate_title}", f"Score: {match.matched_post_score}"]
    else:
        confirmation_lines += ["", "No live candidate matched yet.", "Add posts in the Posts section to start matching."]

    if matched_post:
        notification_lines = [
            f"New pitch received for your request: {title}",
            "",
            f"Matched post: {candidate_title}",
            candidate_summary,
            f"View in Qwoted: {request_url}",
        ]
    else:
        notification_lines = [
            f"New pitch received for your request: {title}",
            "",
            "No live candidate matched yet.",
            "Create a post in the Posts section to start receiving replies.",
            f"View in Qwoted: {request_url}",
        ]

    return {
        "requestId": request_id,
        "requestUrl": request_url,
        "confirmation": "\n".join(confirmation_lines),
        "notification": "\n".join(notification_lines),
        "matchedPost": matched_post,
        "matchedPostScore": match.matched_post_score,
    }
