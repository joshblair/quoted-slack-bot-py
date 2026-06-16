"""
MongoDB access layer.

Shares the same database and collection schema as the TypeScript app so both
services can read and write the same data without any migration.

Collections
-----------
users        — Qwoted accounts (email, passwordHash, slackTeamId, slackUserId, …)
sessions     — login session tokens with a 7-day TTL
posts        — media request catalog used for Slack matching
action_logs  — append-only audit trail of Slack and web events
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from bson import ObjectId
from pymongo import MongoClient, DESCENDING
from pymongo.collection import Collection
from pymongo.errors import OperationFailure

from bot.config import get_config

_client: Optional[MongoClient] = None


def _get_client() -> MongoClient:
    global _client
    if _client is None:
        config = get_config()
        if not config.mongo_uri:
            raise RuntimeError("MONGODB_URI is not configured.")
        _client = MongoClient(config.mongo_uri)
    return _client


def _db():
    return _get_client().get_default_database()


def _try_create_index(col: Collection, *args, **kwargs) -> None:
    try:
        col.create_index(*args, **kwargs)
    except OperationFailure:
        pass


def _users() -> Collection:
    col = _db()["users"]
    _try_create_index(col, "email", unique=True)
    _try_create_index(col, [("slackTeamId", 1), ("slackUserId", 1)], unique=True, sparse=True)
    return col


def _sessions() -> Collection:
    col = _db()["sessions"]
    _try_create_index(col, "token", unique=True)
    _try_create_index(col, "expiresAt", expireAfterSeconds=0)
    return col


def _posts() -> Collection:
    col = _db()["posts"]
    col.create_index([("createdAt", DESCENDING)], background=True)
    return col


def _logs() -> Collection:
    col = _db()["action_logs"]
    col.create_index([("createdAt", DESCENDING)], background=True)
    return col


# ---------------------------------------------------------------------------
# Password hashing
# Node crypto.scrypt defaults: N=16384, r=8, p=1.
# Python hashlib.scrypt uses the same defaults, so hashes are cross-compatible.
# ---------------------------------------------------------------------------

def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=64)
    return f"{salt.hex()}:{key.hex()}"


def _verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt_hex, key_hex = stored_hash.split(":")
        salt = bytes.fromhex(salt_hex)
        stored = bytes.fromhex(key_hex)
        derived = hashlib.scrypt(
            password.encode(), salt=salt, n=16384, r=8, p=1, dklen=len(stored)
        )
        return secrets.compare_digest(derived, stored)
    except Exception:
        return False


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _doc_to_user(doc: dict) -> dict:
    return {
        "id": str(doc["_id"]),
        "email": doc["email"],
        "name": doc["name"],
        "qwotedUserId": doc.get("qwotedUserId", ""),
        "slackTeamId": doc.get("slackTeamId"),
        "slackUserId": doc.get("slackUserId"),
        "createdAt": doc.get("createdAt"),
        "updatedAt": doc.get("updatedAt"),
        "linkedAt": doc.get("linkedAt"),
    }


def _doc_to_post(doc: dict) -> dict:
    return {
        "id": str(doc["_id"]),
        "ownerUserId": doc.get("ownerUserId", ""),
        "title": doc["title"],
        "summary": doc.get("summary", ""),
        "mode": doc["mode"],
        "requestedBy": doc.get("requestedBy", ""),
        "deadline": doc.get("deadline", ""),
        "category": doc.get("category", ""),
        "status": doc.get("status", "open"),
        "createdAt": doc.get("createdAt"),
        "updatedAt": doc.get("updatedAt"),
    }


# ---------------------------------------------------------------------------
# Seed posts — loaded automatically when the posts collection is empty
# ---------------------------------------------------------------------------

_SEED_POSTS = [
    {
        "title": "Gas prices and household budgets",
        "summary": "A reporter is looking for economists or energy experts to explain the latest price changes.",
        "mode": "experts",
        "requestedBy": "Qwoted editorial team",
        "deadline": "Friday",
        "category": "Newsroom",
        "status": "open",
    },
    {
        "title": "Best laptop for remote newsroom work",
        "summary": "Editors want lightweight laptops with strong battery life and quiet keyboards.",
        "mode": "products",
        "requestedBy": "Qwoted editorial team",
        "deadline": "Monday",
        "category": "Tech",
        "status": "open",
    },
    {
        "title": "Small business lending trends",
        "summary": "Need a banking or lending expert to comment on loan demand and approval trends.",
        "mode": "experts",
        "requestedBy": "Qwoted editorial team",
        "deadline": "Wednesday",
        "category": "Finance",
        "status": "open",
    },
    {
        "title": "Wireless microphone recommendations",
        "summary": "Looking for compact wireless mics with reliable range for field reporting.",
        "mode": "products",
        "requestedBy": "Qwoted editorial team",
        "deadline": "Thursday",
        "category": "Audio",
        "status": "open",
    },
    {
        "title": "Healthcare staffing shortages",
        "summary": "Seeking hospital operations experts who can speak to staffing and retention.",
        "mode": "experts",
        "requestedBy": "Qwoted editorial team",
        "deadline": "Today",
        "category": "Health",
        "status": "open",
    },
    {
        "title": "Best podcast recorder",
        "summary": "Need a recorder with clean preamps and easy file transfer for reporting travel.",
        "mode": "products",
        "requestedBy": "Qwoted editorial team",
        "deadline": "Next week",
        "category": "Audio",
        "status": "open",
    },
    {
        "title": "Local housing affordability",
        "summary": "Looking for housing economists and policy experts who can explain rent pressure.",
        "mode": "experts",
        "requestedBy": "Qwoted editorial team",
        "deadline": "Friday",
        "category": "Policy",
        "status": "open",
    },
    {
        "title": "Mobile hotspot devices",
        "summary": "Requesting recommendations for dependable hotspots for travel reporting.",
        "mode": "products",
        "requestedBy": "Qwoted editorial team",
        "deadline": "Tomorrow",
        "category": "Connectivity",
        "status": "open",
    },
    {
        "title": "Climate change impact on agriculture",
        "summary": "Seeking an agriculture or climate expert with strong reporting credentials.",
        "mode": "experts",
        "requestedBy": "Qwoted editorial team",
        "deadline": "Tuesday",
        "category": "Environment",
        "status": "open",
    },
    {
        "title": "Desk chair for long edit days",
        "summary": "Looking for ergonomic chairs with strong lumbar support for editors.",
        "mode": "products",
        "requestedBy": "Qwoted editorial team",
        "deadline": "Friday",
        "category": "Workspace",
        "status": "open",
    },
]


def _ensure_seed_posts(col: Collection) -> None:
    if col.count_documents({}) == 0:
        now = datetime.now(timezone.utc).isoformat()
        col.insert_many(
            [
                {**p, "ownerUserId": f"seed-post-owner-{i + 1}", "createdAt": now, "updatedAt": now}
                for i, p in enumerate(_SEED_POSTS)
            ]
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_users() -> list[dict]:
    docs = list(_users().find({}).sort("createdAt", DESCENDING))
    return [_doc_to_user(d) for d in docs]


def list_posts() -> list[dict]:
    col = _posts()
    _ensure_seed_posts(col)
    docs = list(col.find({}).sort("createdAt", DESCENDING))
    return [_doc_to_post(d) for d in docs]


def create_post(
    owner_user_id: str,
    title: str,
    summary: str = "",
    mode: str = "experts",
    requested_by: str = "",
    deadline: str = "",
    category: str = "",
    status: str = "open",
) -> dict:
    if not owner_user_id or not title.strip():
        raise ValueError("Owner and title are required.")

    col = _posts()
    _ensure_seed_posts(col)
    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "ownerUserId": owner_user_id,
        "title": title.strip(),
        "summary": summary.strip(),
        "mode": mode,
        "requestedBy": requested_by.strip(),
        "deadline": deadline.strip(),
        "category": category.strip(),
        "status": status,
        "createdAt": now,
        "updatedAt": now,
    }
    result = col.insert_one(doc)
    doc["_id"] = result.inserted_id
    return _doc_to_post(doc)


def create_user(name: str, email: str, password: str) -> dict:
    name = name.strip()
    email = _normalize_email(email)
    if not name or not email or not password:
        raise ValueError("Name, email, and password are required.")

    col = _users()
    if col.find_one({"email": email}):
        raise ValueError("Email already exists.")

    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "email": email,
        "name": name,
        "passwordHash": _hash_password(password),
        "qwotedUserId": f"demo-user-{secrets.token_hex(6)}",
        "slackTeamId": None,
        "slackUserId": None,
        "createdAt": now,
        "updatedAt": now,
        "linkedAt": None,
    }
    result = _users().insert_one(doc)
    doc["_id"] = result.inserted_id
    return _doc_to_user(doc)


def authenticate_user(email: str, password: str) -> Optional[dict]:
    if not email.strip() or not password:
        return None
    doc = _users().find_one({"email": _normalize_email(email)})
    if not doc:
        return None
    if not _verify_password(password, doc.get("passwordHash", "")):
        return None
    return _doc_to_user(doc)


def create_session(user_id: str) -> str:
    token = secrets.token_hex(32)
    now = datetime.now(timezone.utc)
    _sessions().insert_one(
        {
            "token": token,
            "userId": user_id,
            "createdAt": now,
            "expiresAt": now + timedelta(days=7),
        }
    )
    return token


def find_user_by_session(token: Optional[str]) -> Optional[dict]:
    if not token:
        return None
    session = _sessions().find_one({"token": token})
    if not session:
        return None
    if session["expiresAt"] < datetime.now(timezone.utc):
        _sessions().delete_one({"token": token})
        return None
    try:
        doc = _users().find_one({"_id": ObjectId(session["userId"])})
    except Exception:
        return None
    return _doc_to_user(doc) if doc else None


def delete_session(token: Optional[str]) -> None:
    if token:
        _sessions().delete_one({"token": token})


def link_slack_account(user_id: str, slack_team_id: str, slack_user_id: str) -> Optional[dict]:
    if not user_id or not slack_team_id or not slack_user_id:
        return None
    try:
        oid = ObjectId(user_id)
    except Exception:
        return None
    now = datetime.now(timezone.utc).isoformat()
    doc = _users().find_one_and_update(
        {"_id": oid},
        {"$set": {"slackTeamId": slack_team_id, "slackUserId": slack_user_id, "linkedAt": now, "updatedAt": now}},
        return_document=True,
    )
    return _doc_to_user(doc) if doc else None


def find_linked_user(team_id: Optional[str], user_id: Optional[str]) -> Optional[dict]:
    if not team_id or not user_id:
        return None
    doc = _users().find_one({"slackTeamId": team_id, "slackUserId": user_id})
    return _doc_to_user(doc) if doc else None


def append_action_log(
    action: str,
    source: str,
    summary: str,
    status: str = "ok",
    actor_user_id: Optional[str] = None,
    actor_email: Optional[str] = None,
    slack_team_id: Optional[str] = None,
    slack_user_id: Optional[str] = None,
    details: Optional[dict] = None,
) -> None:
    try:
        _logs().insert_one(
            {
                "action": action,
                "source": source,
                "actorUserId": actor_user_id,
                "actorEmail": actor_email,
                "slackTeamId": slack_team_id,
                "slackUserId": slack_user_id,
                "status": status,
                "summary": summary,
                "details": details or {},
                "createdAt": datetime.now(timezone.utc),
            }
        )
    except Exception:
        pass


def list_action_logs(limit: int = 50) -> list[dict]:
    limit = max(1, min(limit, 200))
    docs = list(_logs().find({}).sort("createdAt", DESCENDING).limit(limit))
    result = []
    for d in docs:
        created = d.get("createdAt")
        result.append(
            {
                "id": str(d["_id"]),
                "action": d.get("action", ""),
                "source": d.get("source", ""),
                "actorUserId": d.get("actorUserId"),
                "actorEmail": d.get("actorEmail"),
                "slackTeamId": d.get("slackTeamId"),
                "slackUserId": d.get("slackUserId"),
                "status": d.get("status", "ok"),
                "summary": d.get("summary", ""),
                "details": d.get("details", {}),
                "createdAt": created.isoformat() if hasattr(created, "isoformat") else str(created),
            }
        )
    return result
