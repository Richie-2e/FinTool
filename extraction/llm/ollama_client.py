"""
extraction/llm/ollama_client.py
Thin HTTP wrapper around the Ollama /api/chat endpoint.

Responsibilities:
  - Send system + user prompt to Ollama
  - Handle connection errors, timeouts, and HTTP errors
  - Strip markdown fences from response (model sometimes wraps JSON)
  - Attempt partial JSON recovery when output is truncated
  - Return (parsed_dict | None, latency_seconds, json_was_valid)

No business logic lives here. Callers supply the complete prompts.
"""

from __future__ import annotations

import json
import re
import time
from typing import Optional

import requests

from extraction.llm.config import (
    OLLAMA_CHAT_ENDPOINT,
    OLLAMA_MODEL,
    OLLAMA_NUM_PREDICT,
    OLLAMA_TEMPERATURE,
    OLLAMA_TIMEOUT_S,
)


def call_ollama(
    system_prompt: str,
    user_text: str,
    model: Optional[str] = None,
    num_predict: Optional[int] = None,
) -> tuple[Optional[dict], float, bool]:
    """
    Call the Ollama chat API with the given prompts.

    Parameters
    ----------
    system_prompt : str
        The system instruction. Should be the statement-specific prompt
        from prompts.py.
    user_text : str
        The user message — typically the page text block.
    model : str, optional
        Overrides OLLAMA_MODEL from config (used in tests / experiments).
    num_predict : int, optional
        Overrides OLLAMA_NUM_PREDICT from config.

    Returns
    -------
    (result, latency_seconds, json_valid)
        result       — parsed JSON dict, or None on failure
        latency_seconds — wall-clock time for the HTTP round-trip
        json_valid   — True if the response was valid unmodified JSON;
                       False if partial recovery was used or call failed
    """
    payload = {
        "model": model or OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_text},
        ],
        "format": "json",
        "stream": False,
        "options": {
            "temperature": OLLAMA_TEMPERATURE,
            "num_predict": num_predict or OLLAMA_NUM_PREDICT,
        },
    }

    t0 = time.perf_counter()

    try:
        resp = requests.post(
            OLLAMA_CHAT_ENDPOINT,
            json=payload,
            timeout=OLLAMA_TIMEOUT_S,
        )
        resp.raise_for_status()
    except requests.exceptions.ConnectionError:
        print(f"  [ollama] ERROR — cannot connect to {OLLAMA_CHAT_ENDPOINT}. Is Ollama running?")
        return None, 0.0, False
    except requests.exceptions.Timeout:
        print(f"  [ollama] ERROR — request timed out after {OLLAMA_TIMEOUT_S}s")
        return None, float(OLLAMA_TIMEOUT_S), False
    except requests.exceptions.HTTPError as e:
        print(f"  [ollama] ERROR — HTTP {e}")
        return None, 0.0, False

    latency = time.perf_counter() - t0
    raw_content: str = resp.json()["message"]["content"]

    # Strip markdown code fences the model occasionally adds
    clean = raw_content.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\n?", "", clean)
        clean = re.sub(r"\n?```$", "", clean)

    try:
        parsed = json.loads(clean)
        return parsed, latency, True
    except json.JSONDecodeError as e:
        print(f"  [ollama] WARN — JSON parse failed: {e} — attempting partial recovery")
        recovered = _recover_partial_json(clean)
        if recovered:
            n = len(recovered.get("metrics", []))
            print(f"  [ollama] WARN — recovered {n} metrics from truncated output")
            return recovered, latency, False
        print(f"  [ollama] WARN — recovery failed. First 300 chars: {raw_content[:300]}")
        return None, latency, False


def _recover_partial_json(text: str) -> Optional[dict]:
    """
    Salvage complete metric objects from a truncated JSON string.
    Walks the character stream tracking brace depth; collects every
    complete top-level {...} block that contains a "canonical_name" key.
    """
    objects: list[dict] = []
    depth = 0
    start = -1

    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start != -1:
                candidate = text[start : i + 1]
                try:
                    obj = json.loads(candidate)
                    if "canonical_name" in obj:
                        objects.append(obj)
                except json.JSONDecodeError:
                    pass
                start = -1

    if objects:
        return {"metrics": objects, "notes": "recovered from truncated output"}
    return None
