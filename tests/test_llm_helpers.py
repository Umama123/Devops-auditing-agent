import pytest

import llm
from llm import (LLMError, QuotaError, Throttle, call_with_retry, create_client,
                 is_daily_quota, parse_retry_delay, to_openai_tools)
from tools import TOOL_SPECS


class Err(Exception):
    def __init__(self, code, msg="boom"):
        super().__init__(msg)
        self.code = code


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)


def test_parse_retry_delay():
    assert parse_retry_delay("Please retry in 13.5s.") == 13.5
    assert parse_retry_delay("{'retryDelay': '27s'}") == 27.0
    assert parse_retry_delay("nothing") is None


def test_daily_quota_detection():
    assert is_daily_quota("GenerateRequestsPerDayPerProjectPerModel-FreeTier")
    assert not is_daily_quota("per minute limit")


def test_retry_then_success():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise Err(429, "retry in 2s")
        return "ok"

    assert call_with_retry(fn, Throttle(0)) == "ok" and calls["n"] == 3


def test_daily_quota_fails_fast_and_non_retryable_raises():
    with pytest.raises(QuotaError):
        call_with_retry(lambda: (_ for _ in ()).throw(Err(429, "PerDay exceeded")), Throttle(0))
    with pytest.raises(LLMError):
        call_with_retry(lambda: (_ for _ in ()).throw(Err(403, "denied")), Throttle(0))


def test_retries_exhausted_raises_quota_error():
    with pytest.raises(QuotaError):
        call_with_retry(lambda: (_ for _ in ()).throw(Err(503)), Throttle(0), max_retries=2)


def test_openai_tool_conversion():
    tools = to_openai_tools(TOOL_SPECS)
    names = [t["function"]["name"] for t in tools]
    assert names == [s["name"] for s in TOOL_SPECS]
    submit = tools[-1]["function"]["parameters"]
    assert submit["type"] == "object"
    item = submit["properties"]["violations"]["items"]["properties"]
    assert item["file"]["type"] == ["string", "null"]
    assert item["line"]["type"] == ["integer", "null"]
    assert tools[0]["function"]["parameters"] == {"type": "object", "properties": {}}


def test_factory_validation():
    with pytest.raises(ValueError):
        create_client("p", TOOL_SPECS, env={"LLM_PROVIDER": "gemini"})
    with pytest.raises(ValueError):
        create_client("p", TOOL_SPECS, env={"LLM_PROVIDER": "openai"})
    with pytest.raises(ValueError):
        create_client("p", TOOL_SPECS, env={"LLM_PROVIDER": "nope"})


def test_factory_builds_clients_with_model_fallback_list():
    c = create_client("p", TOOL_SPECS, env={"GEMINI_API_KEY": "k", "GEMINI_MODEL": "a, b"})
    assert c.models == ["a", "b"]
    o = create_client("p", TOOL_SPECS, env={"LLM_PROVIDER": "openai", "OPENAI_API_KEY": "k"})
    assert "openai-compatible" in o.describe()
