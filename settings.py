"""Read optional local .env without an extra dependency or exposing keys to UI."""
import os
from pathlib import Path

from llm import DEFAULT_MODELS


ENV_FILE = Path(__file__).resolve().parent / ".env"
KEY_NAMES = {"deepseek": "DEEPSEEK_API_KEY", "gpt": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY"}
# Edit these two values to change the chat limits. The server enforces both.
MAX_PLAN_TURNS = 15
MAX_USER_MESSAGE_CHARS = 1000


def risk_review_enabled(env=None):
    """Return whether the optional second-pass model risk review is enabled.

    The plan response always contains an AI risk assessment.  This switch adds
    a separate audit call for deployments that want a stricter, two-pass
    policy.  It is deliberately opt-in because it adds one API request per
    plan and one request per browser click.
    """
    raw = setting("AI_RPA_RISK_REVIEW", env).strip().lower()
    return raw in {"1", "true", "yes", "on", "ai"}


def read_env(path=ENV_FILE):
    values = {}
    if path.exists():
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            name = name.strip()
            value = value.strip().strip('"').strip("'")
            if name:
                values[name] = value
    return values


def setting(name, env=None):
    values = env if env is not None else read_env()
    return os.environ.get(name) or values.get(name, "")


def resolve_model_config(data, env=None):
    values = env if env is not None else read_env()
    provider = (data.get("provider") or setting("AI_RPA_PROVIDER", values) or "deepseek").strip()
    if provider not in DEFAULT_MODELS:
        raise ValueError("仅支持 deepseek、gpt、gemini")
    model = (data.get("model") or setting("AI_RPA_MODEL", values) or DEFAULT_MODELS[provider]).strip()
    # When switching provider in the UI, a different provider's .env model is not applicable.
    configured_provider = setting("AI_RPA_PROVIDER", values) or "deepseek"
    if provider != configured_provider and not data.get("model"):
        model = DEFAULT_MODELS[provider]
    key = (data.get("api_key") or setting(KEY_NAMES[provider], values)).strip()
    return provider, key, model


def max_actions(env=None):
    raw = setting("AI_RPA_MAX_ACTIONS", env)
    try:
        amount = int(raw or 30)
    except ValueError as exc:
        raise ValueError("AI_RPA_MAX_ACTIONS 必须为正整数") from exc
    if amount < 1:
        raise ValueError("AI_RPA_MAX_ACTIONS 必须为正整数")
    return amount


def public_config(env=None):
    values = env if env is not None else read_env()
    provider = setting("AI_RPA_PROVIDER", values) or "deepseek"
    if provider not in DEFAULT_MODELS:
        provider = "deepseek"
    return {"provider": provider,
            "model": setting("AI_RPA_MODEL", values) or DEFAULT_MODELS[provider],
            "configured_keys": {p: bool(setting(name, values)) for p, name in KEY_NAMES.items()},
            "max_actions": max_actions(values),
            "risk_review": risk_review_enabled(values),
            "max_plan_turns": MAX_PLAN_TURNS,
            "max_user_message_chars": MAX_USER_MESSAGE_CHARS}
