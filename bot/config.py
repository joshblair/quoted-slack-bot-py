import os
from dataclasses import dataclass


@dataclass
class Config:
    slack_bot_token: str
    slack_signing_secret: str
    mongo_uri: str
    app_base_url: str
    demo_request_base_url: str
    session_cookie_name: str


def get_config() -> Config:
    vercel_url = os.environ.get("VERCEL_URL")
    default_base = f"https://{vercel_url}" if vercel_url else "http://localhost:3000"

    return Config(
        slack_bot_token=os.environ.get("SLACK_BOT_TOKEN", ""),
        slack_signing_secret=os.environ.get("SLACK_SIGNING_SECRET", ""),
        mongo_uri=os.environ.get("MONGODB_URI", ""),
        app_base_url=os.environ.get("APP_BASE_URL", default_base),
        demo_request_base_url=os.environ.get(
            "DEMO_REQUEST_BASE_URL", "https://demo.qwoted.com/request"
        ),
        session_cookie_name=os.environ.get("SESSION_COOKIE_NAME", "qwoted_session"),
    )
