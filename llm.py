"""LLM clients (Gemini + any OpenAI-compatible API) with rate-limit handling.

Automatic function calling is NOT used: agent_loop.py executes every tool call.
Features: request throttling, honours server "retry in Xs" hints, model fallback
(Gemini), and clear errors when a daily quota is exhausted.
"""
import json
import os
import re
import time
from typing import Optional

from agent_loop import Turn
from tools import redact

# Optional provider SDKs – imported at module level so type-checkers (Pylance)
# can resolve types.  A clear error is raised at runtime if a missing package
# is actually needed.
try:
    from openai import OpenAI as _OpenAI
except ImportError:  # pragma: no cover
    _OpenAI = None  # type: ignore[assignment,misc]

try:
    from google import genai as _genai
    from google.genai import types as _gtypes
except ImportError:  # pragma: no cover
    _genai = None  # type: ignore[assignment]
    _gtypes = None  # type: ignore[assignment]


class LLMError(RuntimeError):
    pass


class QuotaError(LLMError):
    """Rate limit / quota / overload that did not clear after retries."""


RETRY_CODES = {429, 500, 502, 503, 504}


def parse_retry_delay(msg):
    m = (re.search(r"try again in ([\d.]+)\s*s", msg, re.I)
         or re.search(r"retry in ([\d.]+)\s*s", msg, re.I)
         or re.search(r"retryDelay['\"]?\s*:\s*['\"]?(\d+(?:\.\d+)?)s", msg))
    return float(m.group(1)) if m else None


def is_tpm_error(msg):
    """Groq and some providers surface tokens-per-minute errors with code 429."""
    low = msg.lower()
    return any(k in low for k in ("tokens per minute", "tpm", "token per minute"))


def is_daily_quota(msg):
    low = msg.lower()
    return any(k in low for k in ("perday", "per day", "daily", "insufficient_quota"))


class Throttle:
    """Guarantee a minimum gap between API calls (keeps us under requests/minute)."""

    def __init__(self, min_interval):
        self.min_interval = max(0.0, float(min_interval))
        self.last = 0.0

    def wait(self):
        gap = self.min_interval - (time.time() - self.last)
        if gap > 0:
            time.sleep(gap)
        self.last = time.time()


def call_with_retry(fn, throttle, max_retries=8):
    for attempt in range(max_retries + 1):
        throttle.wait()
        try:
            return fn()
        except Exception as e:
            msg = str(e)
            # OpenAI SDK: e.status_code is the numeric HTTP code (429, 500 …)
            # e.code is a string like 'rate_limit_exceeded' – do NOT use it as
            # the numeric code or the `code in RETRY_CODES` check always fails.
            status = getattr(e, "status_code", None)
            if not isinstance(status, int):
                # Some providers (httpx, requests) store it on .response.status_code
                status = getattr(getattr(e, "response", None), "status_code", None)
            if not isinstance(status, int):
                # Last resort: try the raw .code attr only when it looks numeric
                _raw = getattr(e, "code", None)
                status = _raw if isinstance(_raw, int) else None
            code = status  # always an int or None from here on
            low = msg.lower()
            if code == 429 and is_daily_quota(msg):
                raise QuotaError(
                    "Daily/billing quota exhausted for this model or key. Wait for the quota "
                    "reset, use another model/key, or enable billing. " + redact(msg)[:200])
            retryable = (
                code in RETRY_CODES
                or "timed out" in low
                or "timeout" in low
                # Groq returns 400 when the model generates malformed tool-call
                # JSON (truncated output). Treat as retryable so we try again.
                or (code == 400 and "tool_use_failed" in low)
            )
            if not retryable:
                raise LLMError(f"LLM API error ({code}): {redact(msg)[:400]}")
            if attempt == max_retries:
                err = QuotaError if code in (429, 503) else LLMError
                raise err(f"LLM API still failing after {max_retries} retries ({code}): {redact(msg)[:300]}")
            hint = parse_retry_delay(msg)
            # For TPM (tokens-per-minute) errors, the "retry in Xs" hint is the
            # window reset – but spamming again right after still exhausts the
            # next window.  Enforce a sensible floor so we don't loop uselessly.
            if code == 429 and is_tpm_error(msg):
                tpm_floor = 62  # wait just over a full minute to let the TPM window reset
                wait = max(tpm_floor, (hint + 2) if hint else tpm_floor)
                print(f"  TPM rate-limit hit; waiting {wait:.0f}s for token window to reset "
                      f"({attempt + 1}/{max_retries})...")
            else:
                backoff = min(5 * 2 ** attempt, 60)
                wait = min(max(backoff, (hint + 1) if hint else 0), 120)
                print(f"  API error {code}; waiting {wait:.0f}s then retrying ({attempt + 1}/{max_retries})...")
            time.sleep(wait)


# ------------------------------------------------------------------- Gemini
class GeminiClient:
    def __init__(self, api_key, models, system_prompt, tool_specs, min_interval=6.0, timeout_s=120):
        if _genai is None or _gtypes is None:
            raise ImportError(
                "google-genai package is not installed. Run: pip install google-genai")
        genai = _genai
        types = _gtypes
        self._types = types
        self.models = [models] if isinstance(models, str) else list(models)
        self.idx = 0
        self.throttle = Throttle(min_interval)
        self.client = genai.Client(
            api_key=api_key, http_options=types.HttpOptions(timeout=timeout_s * 1000))
        self.config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            tools=[types.Tool(function_declarations=[
                types.FunctionDeclaration(**spec) for spec in tool_specs])],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            temperature=0.2,
        )
        self.history = []

    def describe(self):
        return f"gemini: {', '.join(self.models)}"

    def send_user(self, text):
        t = self._types
        self.history.append(t.Content(role="user", parts=[t.Part.from_text(text=text)]))
        return self._generate()

    def send_tool_results(self, results):
        t = self._types
        parts = [t.Part.from_function_response(name=n, response=r) for n, r in results]
        self.history.append(t.Content(role="user", parts=parts))
        return self._generate()

    def _generate(self):
        while True:
            try:
                resp = call_with_retry(
                    lambda: self.client.models.generate_content(
                        model=self.models[self.idx], contents=self.history, config=self.config),
                    self.throttle)
                break
            except QuotaError:
                if self.idx + 1 < len(self.models):
                    print(f"  {self.models[self.idx]} unavailable -> switching to {self.models[self.idx + 1]}")
                    self.idx += 1
                    continue
                raise
        if not resp.candidates or resp.candidates[0].content is None:
            raise LLMError("Gemini returned an empty response (possibly blocked)")
        content = resp.candidates[0].content
        self.history.append(content)  # keep original content (incl. thought signatures)
        calls, texts = [], []
        for p in content.parts or []:
            if p.function_call:
                calls.append((p.function_call.name, dict(p.function_call.args or {})))
            elif p.text and not getattr(p, "thought", False):
                texts.append(p.text)
        return Turn(text="\n".join(texts), calls=calls)


# ------------------------------------------------- OpenAI-compatible providers
def _conv_schema(s):
    if isinstance(s, dict):
        out = {}
        for k, v in s.items():
            if k == "nullable":
                continue
            out[k] = v.lower() if k == "type" and isinstance(v, str) else _conv_schema(v)
        if s.get("nullable") and "type" in out:
            out["type"] = [out["type"], "null"]
        return out
    if isinstance(s, list):
        return [_conv_schema(x) for x in s]
    return s


def to_openai_tools(specs):
    tools = []
    for sp in specs:
        params = _conv_schema(sp.get("parameters")) or {"type": "object", "properties": {}}
        tools.append({"type": "function", "function": {
            "name": sp["name"], "description": sp["description"], "parameters": params}})
    return tools


class OpenAICompatClient:
    """Works with OpenAI, Groq, OpenRouter, DeepSeek, Ollama... (set OPENAI_BASE_URL)."""

    def __init__(self, api_key, model, system_prompt, tool_specs, base_url=None, min_interval=1.0):
        if _OpenAI is None:
            raise ImportError(
                "openai package is not installed. Run: pip install openai")
        OpenAI = _OpenAI
        self.model = model
        self.base_url = base_url
        self.client = OpenAI(api_key=api_key, base_url=base_url or None, max_retries=0, timeout=120)
        self.tools = to_openai_tools(tool_specs)
        self.messages = [{"role": "system", "content": system_prompt}]
        self.pending_ids = []
        self.throttle = Throttle(min_interval)

    def describe(self):
        return f"openai-compatible: {self.model}" + (f" @ {self.base_url}" if self.base_url else "")

    def send_user(self, text):
        self.messages.append({"role": "user", "content": text})
        return self._generate()

    def send_tool_results(self, results):
        for (_, payload), cid in zip(results, self.pending_ids):
            self.messages.append({"role": "tool", "tool_call_id": cid,
                                  "content": json.dumps(payload)})
        return self._generate()

    def _generate(self):
        resp = call_with_retry(
            lambda: self.client.chat.completions.create(
                model=self.model,
                messages=self.messages,
                tools=self.tools,
                max_tokens=4096,   # prevents truncated JSON in tool-call arguments
            ),
            self.throttle)
        if not resp.choices:
            raise LLMError("LLM returned no choices")
        msg = resp.choices[0].message
        entry = {"role": "assistant", "content": msg.content or ""}
        calls, ids = [], []
        for tc in msg.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append((tc.function.name, args))
            ids.append(tc.id)
        if msg.tool_calls:
            entry["tool_calls"] = [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"}}
                for tc in msg.tool_calls]
        self.messages.append(entry)
        self.pending_ids = ids
        return Turn(text=msg.content or "", calls=calls)


# ------------------------------------------------------------------ factory
def create_client(system_prompt, tool_specs, env=None):
    env = os.environ if env is None else env
    provider = env.get("LLM_PROVIDER", "gemini").strip().lower()
    if provider == "gemini":
        key = env.get("GEMINI_API_KEY", "").strip()
        if not key:
            raise ValueError("GEMINI_API_KEY is not set (see .env.example)")
        models = [m.strip() for m in env.get("GEMINI_MODEL", "gemini-3.5-flash").split(",") if m.strip()]
        interval = float(env.get("LLM_MIN_INTERVAL", "6"))
        return GeminiClient(key, models, system_prompt, tool_specs, min_interval=interval)
    if provider == "openai":
        key = env.get("OPENAI_API_KEY", "").strip()
        if not key:
            raise ValueError("OPENAI_API_KEY is not set (see .env.example)")
        model = env.get("OPENAI_MODEL", "gpt-4o-mini").strip()
        interval = float(env.get("LLM_MIN_INTERVAL", "1"))
        return OpenAICompatClient(key, model, system_prompt, tool_specs,
                                  base_url=env.get("OPENAI_BASE_URL", "").strip() or None,
                                  min_interval=interval)
    raise ValueError(f"Unknown LLM_PROVIDER '{provider}'. Use 'gemini' or 'openai'.")
