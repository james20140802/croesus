"""
OpenAI-compatible chat-completions client for local LLMs (Sprint 010).

The research agent talks the OpenAI ``/chat/completions`` protocol, which
every mainstream local launcher serves: Ollama (default,
``http://localhost:11434/v1``), LM Studio, llama.cpp server, vLLM, and
MLX-based servers. Swapping launchers is a ``CROESUS_LLM_BASE_URL`` change —
no code change.

Configuration (env vars, overridable per-instance):
  - ``CROESUS_LLM_BASE_URL``  default ``http://localhost:11434/v1``
  - ``CROESUS_LLM_MODEL``     default ``qwen3:32b`` (downscale to 14b/8b on
    smaller hardware)
  - ``CROESUS_LLM_TIMEOUT``   request timeout in seconds, default 600 — a
    30B-class thinking model on consumer hardware routinely needs minutes per
    note, and with ``stream=False`` the server only answers once generation
    finishes

Error contract: :class:`LlmUnavailable` means the server itself cannot serve
us (down, wrong URL, model not pulled) — callers should skip the whole run
with a warning. :class:`LlmError` covers per-request failures worth retrying
on the next asset.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Protocol

DEFAULT_BASE_URL = "http://localhost:11434/v1"
DEFAULT_MODEL = "qwen3:32b"
DEFAULT_TIMEOUT_SECONDS = 600.0


class LlmError(RuntimeError):
    """A single chat request failed; later requests may still succeed."""


class LlmUnavailable(LlmError):
    """The LLM server cannot serve requests at all (down / wrong URL / no model)."""


class ChatClient(Protocol):
    base_url: str
    model: str

    def chat(self, messages: list[dict[str, str]]) -> str:
        """Send a chat-completions request and return the assistant text."""
        ...


class ChatCompletionsClient:
    """Plain-stdlib client for any OpenAI-compatible ``/chat/completions``."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.base_url = (
            base_url or os.getenv("CROESUS_LLM_BASE_URL") or DEFAULT_BASE_URL
        ).rstrip("/")
        self.model = model or os.getenv("CROESUS_LLM_MODEL") or DEFAULT_MODEL
        self.timeout = (
            timeout
            if timeout is not None
            else float(os.getenv("CROESUS_LLM_TIMEOUT") or DEFAULT_TIMEOUT_SECONDS)
        )

    def chat(self, messages: list[dict[str, str]]) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            # Research notes should be reproducible-ish, not creative.
            "temperature": 0.2,
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            if exc.code == 404:
                # Ollama answers 404 both for a wrong path and a missing model.
                raise LlmUnavailable(
                    f"LLM server at {self.base_url} returned 404 — model "
                    f"{self.model!r} may not be installed (try `ollama pull "
                    f"{self.model}`) or the base URL is wrong: {detail}"
                ) from exc
            raise LlmError(
                f"LLM request failed with HTTP {exc.code} at {self.base_url}: {detail}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # A timeout means the server exists but generation outran the
            # budget (model loading, or a big thinking model on small
            # hardware) — a different fix than a down server.
            reason = getattr(exc, "reason", exc)
            if isinstance(exc, TimeoutError) or isinstance(reason, TimeoutError):
                raise LlmUnavailable(
                    f"LLM request to {self.base_url} timed out after "
                    f"{self.timeout:.0f}s — the model may still be loading or "
                    "is too slow for this hardware; raise CROESUS_LLM_TIMEOUT "
                    "or set a smaller CROESUS_LLM_MODEL"
                ) from exc
            raise LlmUnavailable(
                f"no LLM server reachable at {self.base_url} ({exc}); start one "
                "(e.g. `ollama serve`) or set CROESUS_LLM_BASE_URL to any "
                "OpenAI-compatible endpoint (LM Studio, llama.cpp, vLLM, MLX)"
            ) from exc

        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LlmError(
                f"unexpected chat-completions response shape from {self.base_url}: "
                f"{str(body)[:300]}"
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise LlmError(f"empty completion from model {self.model!r}")
        return content
