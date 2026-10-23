import json

import pytest

from agent_loop import AgentError, Turn, run_agent
from tools import RepoTools


class FakeLLM:
    """Scripted stand-in for the LLM: each next turn depends on the previous tool result."""

    def __init__(self, script):
        self.script, self.i, self.received = script, 0, []

    def _next(self):
        t = self.script[min(self.i, len(self.script) - 1)]
        self.i += 1
        return t

    def send_user(self, text):
        self.received.append(text)
        return self._next()

    def send_tool_results(self, results):
        self.received.append(results)
        return self._next()


@pytest.fixture
def tools(tmp_path):
    (tmp_path / "README.md").write_text("hi\n")
    return RepoTools(tmp_path)


def test_loop_executes_tools_then_submits(tools, tmp_path):
    viol = [{"rule_id": "X-1"}]
    llm = FakeLLM([
        Turn(calls=[("list_files", {})]),
        Turn(calls=[("read_file", {"path": "README.md"})]),
        Turn(calls=[("submit_report", {"violations": viol})]),
    ])
    trace = tmp_path / "t.jsonl"
    out = run_agent("u", tools, llm, trace_path=str(trace), log=lambda *_: None)
    assert out == viol
    names = [json.loads(l)["tool"] for l in trace.read_text().splitlines()]
    assert names == ["list_files", "read_file", "submit_report"]
    # the tool result was fed back to the LLM
    assert "README.md" in str(llm.received[1])


def test_unknown_tool_does_not_crash(tools):
    llm = FakeLLM([Turn(calls=[("hack", {})]), Turn(calls=[("submit_report", {"violations": []})])])
    assert run_agent("u", tools, llm, log=lambda *_: None) == []
    assert "unknown tool" in str(llm.received[1])


def test_budget_exhaustion_forces_submit_or_errors(tools):
    llm = FakeLLM([Turn(calls=[("list_files", {})])])
    with pytest.raises(AgentError):
        run_agent("u", tools, llm, max_steps=3, log=lambda *_: None)


def test_text_only_replies_are_nudged_then_fail(tools):
    llm = FakeLLM([Turn(text="thinking")])
    with pytest.raises(AgentError):
        run_agent("u", tools, llm, log=lambda *_: None)
