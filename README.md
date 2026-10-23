# DevOps Repository Auditing Agent

An AI agent (pure Python, no LangChain/LangGraph/n8n) that clones a public GitHub
repository, investigates it with tools chosen by an LLM, and writes an evidence-based
JSON report of DevOps best-practice violations.

## Architecture

```
agent.py        CLI: validates input, clones repo, runs loop, writes report, cleans up
agent_loop.py   Explicit loop: LLM picks tool -> we execute -> result fed back -> repeat
llm.py          Gemini wrapper (automatic function calling disabled; we dispatch tools)
tools.py        list_files, read_file, search_code, git_log + secret redaction + path safety
prompts.py      System prompt: categories, evidence rules, false-positive guidance
report.py       Validates/deduplicates findings, enforces JSON schema, atomic write
tests/          pytest suite (tools, report, agent loop with a scripted fake LLM)
```

Cycle: **Observe** (tool result) -> **Reason** (LLM) -> **Select tool** (LLM function call)
-> **Execute** (`RepoTools.execute`) -> **Interpret** (result returned to LLM) -> **Report**
(`submit_report`). Every tool call is logged to `<output>.trace.jsonl`.

## Safety
- Repo is cloned shallowly into a temp dir, read-only inspected, then deleted.
- No code from the target repository is ever executed (no pytest/ruff runs).
- Path traversal and `.git` internals are blocked; only `https://github.com/<owner>/<repo>` URLs accepted.
- Secret values are redacted from tool output, logs and the report.
- Findings citing non-existent files/lines are corrected to `null`; duplicates removed.

## Setup
```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then put your key in .env
python list_models.py       # (Gemini) pick model name(s) for GEMINI_MODEL
```
Requires `git` on PATH and Python 3.10+.

## LLM providers and rate limits
Set `LLM_PROVIDER` in `.env`:
- `gemini` (default): `GEMINI_API_KEY`, `GEMINI_MODEL` (comma-separated list = automatic fallback models).
- `openai`: `OPENAI_API_KEY`, `OPENAI_MODEL`, optional `OPENAI_BASE_URL` for any OpenAI-compatible API (Groq, OpenRouter, ...).

The client throttles requests (`LLM_MIN_INTERVAL`), honours the server's "retry in Xs" hint,
retries 429/5xx with backoff, switches to the next Gemini model when one is exhausted, and
fails with a clear message when a daily quota is used up. The agent batches several tool
calls per LLM turn to keep the number of API requests low.

## Run
```bash
python agent.py --repo https://github.com/owner/repo --output report.json
```
Options: `--max-steps 25`, `--trace path.jsonl`. Exit code 0 on success, non-zero with a message on failure.

## Test
```bash
pytest -q
```

## Output format
```json
{"repository": "...", "violations": [{"rule_id": "SEC-001", "category": "security",
  "severity": "critical", "file": "config.py", "line": 17, "description": "...",
  "evidence": "...", "recommendation": "..."}]}
```

## Limitations
Shallow clone (last 100 commits); remote branch protection cannot be inferred from files;
LLM judgement can still produce occasional false positives/negatives.
