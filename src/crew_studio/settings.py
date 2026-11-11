"""Central configuration. Everything can be overridden from `.env`."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=False)

# One cheap model for the whole team keeps cost low.
MODEL = os.getenv("STUDIO_MODEL", "openai/gpt-5.4-mini")

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
