from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_DEFAULT_LLM_MODEL = "gpt-6-luna"


def _resolve_llm_model() -> str:
    """LLM_MODEL from the environment, defaulting to the OpenAI model.

    A leftover pre-migration Gemini model name (e.g. in an old local .env)
    must never be sent to OpenAI, so it falls back to the default.
    """
    value = os.getenv("LLM_MODEL", "").strip()
    if not value or value.lower().startswith("gemini"):
        return _DEFAULT_LLM_MODEL
    return value


# Optional at startup — validated in call_llm() when /chat is hit
OPENAI_API_KEY:      str  = os.getenv("OPENAI_API_KEY", "")
DATABASE_URL:        str  = os.getenv("DATABASE_URL",   "sqlite:///./fintool.db")
REDIS_URL:           str  = os.getenv("REDIS_URL",      "redis://localhost:6379/0")
EMBED_MODEL:         str  = os.getenv("EMBED_MODEL",    "sentence-transformers/all-MiniLM-L6-v2")
LLM_MODEL:           str  = _resolve_llm_model()
MAX_FILE_SIZE_BYTES: int  = int(os.getenv("MAX_FILE_SIZE_BYTES", str(52_428_800)))
UPLOAD_DIR:          Path = Path(os.getenv("UPLOAD_DIR",  "uploads"))
OUTPUT_DIR:          Path = Path(os.getenv("OUTPUT_DIR",  "extraction_outputs"))

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
