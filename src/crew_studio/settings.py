"""Central configuration. Everything can be overridden from `.env`."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=False)

# ----------------------------------------------------------------------------- LLM
# STUDIO_PROVIDER picks the provider; STUDIO_MODEL (optional) the model for the whole team;
# STUDIO_MANAGER_MODEL (optional) a different model for the Engineering Lead / Architect.
#
#   provider     key                              default model
#   openai       OPENAI_API_KEY                   gpt-5.4-mini
#   anthropic    ANTHROPIC_API_KEY                claude-opus-5-5
#   openrouter   OPENROUTER_API_KEY               openai/gpt-5.4-mini   (any model: anthropic/..., x-ai/...)
#   grok         XAI_API_KEY (or GROK_API_KEY)    grok-4.7
#   gemini       GEMINI_API_KEY (or GOOGLE_API_KEY) gemini-2.5-flash
#   deepseek     DEEPSEEK_API_KEY                 deepseek-chat
#   ollama       none (local)                     llama3.2
PROVIDERS: dict[str, dict] = {
    "openai": {"prefix": "openai", "keys": ("OPENAI_API_KEY",), "default": "gpt-5.4-mini"},
    "anthropic": {"prefix": "anthropic", "keys": ("ANTHROPIC_API_KEY",), "default": "claude-opus-5-5"},
    "openrouter": {"prefix": "openrouter", "keys": ("OPENROUTER_API_KEY",), "default": "openai/gpt-5.4-mini"},
    # xAI is OpenAI-compatible: routed through CrewAI's native OpenAI client with xAI's base URL
    "grok": {"prefix": "openai", "keys": ("XAI_API_KEY", "GROK_API_KEY"), "default": "grok-4.7",
             "base_url": "https://api.x.ai/v1"},
    "gemini": {"prefix": "gemini", "keys": ("GEMINI_API_KEY", "GOOGLE_API_KEY"), "default": "gemini-2.5-flash"},
    "deepseek": {"prefix": "deepseek", "keys": ("DEEPSEEK_API_KEY",), "default": "deepseek-chat"},
    "ollama": {"prefix": "ollama", "keys": (), "default": "llama3.2"},
}

_legacy = os.getenv("STUDIO_MODEL", "")
# Backwards compatible: STUDIO_MODEL=openai/gpt-5.4-mini (provider prefix) still works.
PROVIDER = os.getenv("STUDIO_PROVIDER", "").lower() or (
    _legacy.split("/", 1)[0] if _legacy.split("/", 1)[0] in PROVIDERS else "openai"
)
if PROVIDER not in PROVIDERS:
    raise SystemExit(f"Unknown STUDIO_PROVIDER '{PROVIDER}'. Choose one of: {', '.join(PROVIDERS)}")


def _resolve_model(raw: str, fallback: str) -> str:
    """Normalise a model name for the chosen provider.

    "openai/gpt-5.4-mini" -> "gpt-5.4-mini" when it repeats the provider name. A model left over
    from another provider (e.g. STUDIO_MODEL=openai/... after switching to grok) is ignored.
    OpenRouter ids such as "openai/gpt-5.4-mini" or "anthropic/claude-..." are kept as-is.
    """
    raw = (raw or "").strip()
    if not raw:
        return fallback
    if PROVIDER == "openrouter":
        return raw.removeprefix("openrouter/")
    head, _, rest = raw.partition("/")
    if rest and head == PROVIDER:
        return rest
    if rest and head in PROVIDERS:
        print(f"⚠️  Ignoring model '{raw}' (it belongs to '{head}', not '{PROVIDER}'); using {fallback}")
        return fallback
    return raw


MODEL = _resolve_model(_legacy, PROVIDERS[PROVIDER]["default"])
MANAGER_MODEL = _resolve_model(os.getenv("STUDIO_MANAGER_MODEL", ""), MODEL)

# Output cap per LLM call for non-OpenAI providers (OpenRouter & co. otherwise reserve the
# model's full window, which fails on low-credit keys). Large enough for a whole app.py.
MAX_TOKENS = int(os.getenv("STUDIO_MAX_TOKENS", "8192"))


def provider_api_key() -> str | None:
    keys = PROVIDERS[PROVIDER]["keys"]
    return next((os.getenv(k) for k in keys if os.getenv(k)), None) if keys else "ollama"


def describe_llm() -> str:
    lead = f", lead={MANAGER_MODEL}" if MANAGER_MODEL != MODEL else ""
    return f"{PROVIDER}:{MODEL}{lead}"

MAX_REVIEW_ROUNDS = int(os.getenv("STUDIO_MAX_REVIEW_ROUNDS", "2"))

RUNNER = os.getenv("STUDIO_RUNNER", "auto").lower()  # auto | docker | local
DOCKER_IMAGE = os.getenv("STUDIO_DOCKER_IMAGE", "ghcr.io/astral-sh/uv:python3.13-bookworm-slim")
RUN_TIMEOUT_SECONDS = int(os.getenv("STUDIO_RUN_TIMEOUT", "180"))

USE_CONTEXT7 = os.getenv("STUDIO_USE_CONTEXT7", "1") == "1"
CONTEXT7_URL = "https://mcp.context7.com/mcp"

WORKSPACES_DIR = Path(os.getenv("STUDIO_WORKSPACES_DIR", PROJECT_ROOT / "workspaces"))
WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)

# Guard rails so a confused agent cannot burn money in an endless tool loop.
AGENT_MAX_ITER = int(os.getenv("STUDIO_AGENT_MAX_ITER", "25"))
MANAGER_MAX_ITER = int(os.getenv("STUDIO_MANAGER_MAX_ITER", "20"))
