"""
extraction/llm/config.py
LLM provider configuration.

To swap models, change OLLAMA_MODEL here. No other file needs to change.
"""

from __future__ import annotations

# Ollama endpoint
OLLAMA_BASE_URL: str = "http://localhost:11434"
OLLAMA_CHAT_ENDPOINT: str = f"{OLLAMA_BASE_URL}/api/chat"

# Model — change this single line to upgrade (e.g. "qwen2.5:7b-instruct")
OLLAMA_MODEL: str = "qwen2.5:3b"

# Inference parameters
OLLAMA_NUM_PREDICT: int = 4096   # max tokens in response; 4096 prevents JSON truncation
OLLAMA_TEMPERATURE: int = 0      # deterministic output
OLLAMA_TIMEOUT_S: int = 180      # per-call HTTP timeout in seconds

# Context window
MAX_CHARS_PER_CALL: int = 10_000  # max characters sent per LLM call
