"""Explicit Observe -> Reason -> Select Tool -> Execute -> Interpret loop (no frameworks).

The LLM client decides which tool to call next; this module executes the tool,
logs a trace, and feeds the (redacted) result back to the LLM.
"""
import json
import os
import time
from dataclasses import dataclass, field



class AgentError(RuntimeError):
    pass


@dataclass
class Turn:
    text: str = ""
    calls: list = field(default_factory=list)  # list of (tool_name, args_dict)


def run_agent(repo_url, tools, llm, max_steps=25, trace_path=None, log=print):
    """Run the investigation and return the raw violation list from submit_report."""
    max_chars = int(os.getenv("TOOL_OUTPUT_MAX_CHARS", "8000"))  # read at run time (after .env is loaded)
    trace = open(trace_path, "w", encoding="utf-8") if trace_path else None

    def record(step, name, args, result):
        if trace:
            trace.write(json.dumps({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "step": step, "tool": name,
                "args": args, "result_preview": str(result)[:300]}) + "\n")
            trace.flush()

    try:
        turn = llm.send_user(
            f"Audit the repository {repo_url}. It is already cloned locally and read-only. "
            "Investigate with the tools, then call submit_report."
        )
        nudges = 0
        for step in range(1, max_steps + 1):
            if not turn.calls:
                nudges += 1
                if nudges > 3:
                    raise AgentError("LLM stopped calling tools without submitting a report")
                turn = llm.send_user(
                    "Continue the investigation with tools, or call submit_report if finished.")
                continue

            for name, args in turn.calls:
                if name == "submit_report":
                    record(step, name, {"n_violations": len((args or {}).get("violations", []))}, "submitted")
                    log(f"[step {step}] submit_report")
                    return list((args or {}).get("violations", []) or [])

            results = []
            for name, args in turn.calls:
                log(f"[step {step}] {name}({json.dumps(args)[:120]})")
                out = tools.execute(name, args)
                if len(out) > max_chars:
                    out = out[:max_chars] + "\n...[output truncated]"
                record(step, name, args, out)
                payload = {"result": out}
                left = max_steps - step
                if left <= 3:
                    payload["note"] = f"Only {left} steps left. Call submit_report soon."
                results.append((name, payload))
            turn = llm.send_tool_results(results)

        # budget exhausted: force a final answer
        turn = llm.send_user("Step budget exhausted. Call submit_report NOW with what you verified.")
        for name, args in turn.calls:
            if name == "submit_report":
                return list((args or {}).get("violations", []) or [])
        raise AgentError("Step budget exhausted and no report was submitted")
    finally:
        if trace:
            trace.close()
