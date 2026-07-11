from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Optional at startup — validated in call_llm() when /chat is hit
GEMINI_API_KEY:      str  = os.getenv("GEMINI_API_KEY", "")
DATABASE_URL:        str  = os.getenv("DATABASE_URL",   "sqlite:///./fintool.db")
REDIS_URL:           str  = os.getenv("REDIS_URL",      "redis://localhost:6379/0")
EMBED_MODEL:         str  = os.getenv("EMBED_MODEL",    "sentence-transformers/all-MiniLM-L6-v2")
LLM_MODEL:           str  = os.getenv("LLM_MODEL",      "gemini-flash-latest")
MAX_FILE_SIZE_BYTES: int  = int(os.getenv("MAX_FILE_SIZE_BYTES", str(52_428_800)))
UPLOAD_DIR:          Path = Path(os.getenv("UPLOAD_DIR",  "uploads"))
OUTPUT_DIR:          Path = Path(os.getenv("OUTPUT_DIR",  "extraction_outputs"))

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
