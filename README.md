# DevOps Repository Auditing Agent

An AI-powered agent (pure Python — no LangChain, LangGraph, or n8n) that clones a public GitHub repository, investigates it using LLM-driven tool calls, and produces an evidence-based JSON report of DevOps best-practice violations.

---

## 🚀 How to Run (Quick Start)

### Prerequisites
- Python **3.10+**
- `git` installed and on your PATH
- A free API key from **[Groq](https://console.groq.com)** (or Google Gemini)

---

### Step 1 — Clone this repo
```bash
git clone https://github.com/Umama123/Devops-auditing-agent.git
cd Devops-auditing-agent
```

### Step 2 — Create a virtual environment
```bash
# Windows
python -m venv venv
venv\Scripts\activate

# macOS / Linux
python3 -m venv venv
source venv/bin/activate
```

### Step 3 — Install dependencies
```bash
pip install -r requirements.txt
```

### Step 4 — Configure your API key
```bash
# Copy the example config file
copy .env.example .env        # Windows
cp .env.example .env          # macOS/Linux
```

Now open `.env` and fill in your values:

```env
LLM_PROVIDER=openai
OPENAI_API_KEY=your_groq_api_key_here
OPENAI_BASE_URL=https://api.groq.com/openai/v1
OPENAI_MODEL=qwen/qwen3.8-27b
LLM_MIN_INTERVAL=15
TOOL_OUTPUT_MAX_CHARS=2000
```

> **Get a free Groq API key:** Go to [console.groq.com](https://console.groq.com) → API Keys → Create new key

### Step 5 — Run the agent
```bash
python agent.py --repo https://github.com/owner/repo --output report.json
```

**Example:**
```bash
python agent.py --repo https://github.com/hammadmajeed/ci_cd_demo --output report.json
```

The agent will:
1. Clone the repository into a temporary folder
2. Use the LLM to investigate files, git history, secrets, CI/CD configs
3. Write a structured JSON report of violations found
4. Auto-delete the cloned repo when done

---

## 📁 Project Structure

```
agent.py          CLI entry point — validates input, clones repo, runs loop, writes report
agent_loop.py     Observe → Reason → Select Tool → Execute → Interpret loop
llm.py            LLM client (Gemini + OpenAI-compatible) with rate-limit handling & retries
tools.py          Tools: list_files, read_file, search_code, git_log + secret redaction
prompts.py        System prompt defining audit categories and evidence rules
report.py         Validates findings, enforces JSON schema, atomic file write
list_models.py    Helper script to list available Gemini models for your API key
tests/            pytest suite (tools, report, agent loop with scripted fake LLM)
.env.example      Template for configuration — copy to .env and fill in your key
```

---

## ⚙️ Configuration (`.env` options)

| Variable | Description | Default |
|---|---|---|
| `LLM_PROVIDER` | `gemini` or `openai` | `gemini` |
| `OPENAI_API_KEY` | Your Groq / OpenAI API key | — |
| `OPENAI_BASE_URL` | Base URL for OpenAI-compatible APIs (e.g. Groq) | OpenAI default |
| `OPENAI_MODEL` | Model name to use | `gpt-4o-mini` |
| `GEMINI_API_KEY` | Your Google Gemini API key | — |
| `GEMINI_MODEL` | Gemini model name(s), comma-separated for fallback | `gemini-2.0-flash` |
| `LLM_MIN_INTERVAL` | Minimum seconds between API calls (free-tier throttle) | `6` |
| `TOOL_OUTPUT_MAX_CHARS` | Max chars of tool output sent to LLM per call | `8000` |

---

## 🤖 LLM Providers

### Option A — Groq (Recommended for free tier)
```env
LLM_PROVIDER=openai
OPENAI_API_KEY=gsk_...your_key...
OPENAI_BASE_URL=https://api.groq.com/openai/v1
OPENAI_MODEL=qwen/qwen3.8-27b
LLM_MIN_INTERVAL=15
TOOL_OUTPUT_MAX_CHARS=2000
```

### Option B — Google Gemini
```env
LLM_PROVIDER=gemini
GEMINI_API_KEY=AIza...your_key...
GEMINI_MODEL=gemini-2.0-flash
LLM_MIN_INTERVAL=6
```

### Option C — OpenAI
```env
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...your_key...
OPENAI_MODEL=gpt-4o-mini
```

---

## 🏗️ Architecture

```
  GitHub Repo URL
        │
        ▼
  agent.py  ──── validates URL, clones repo (read-only temp dir)
        │
        ▼
  agent_loop.py  ──── Observe → Reason → Select Tool → Execute → Interpret
        │                                    │
        │                              llm.py (LLM call)
        │                                    │
        │                         tool selected by LLM
        │                                    │
        ▼                              tools.py executes it
  submit_report ◄──────── LLM done investigating
        │
        ▼
  report.py  ──── validates, deduplicates, writes report.json
```

**Agent loop steps (per turn):**
1. **Observe** — previous tool result is in context
2. **Reason** — LLM decides what to look at next
3. **Select Tool** — LLM emits a function call
4. **Execute** — `RepoTools.execute()` runs it safely
5. **Interpret** — result fed back to LLM
6. Repeat until LLM calls `submit_report`

---

## 📊 Output Format

The agent writes a `report.json` file:

```json
{
  "repository": "https://github.com/owner/repo",
  "violations": [
    {
      "rule_id": "SEC-001",
      "category": "security",
      "severity": "critical",
      "file": "config.py",
      "line": 17,
      "description": "Hardcoded API key found in source file",
      "evidence": "Line 17: API_KEY = 'sk-abc123...' committed directly",
      "recommendation": "Move secrets to environment variables and rotate the exposed key"
    }
  ]
}
```

**Violation categories:** `security`, `ci_cd`, `dependency_management`, `repository_hygiene`, `testing`  
**Severity levels:** `critical`, `high`, `medium`, `low`

---

## 🧪 Running Tests

```bash
pytest -q
```

---

## 🔒 Safety Features

- Cloned repo is **read-only** — no code from the target repo is ever executed
- Only `https://github.com/<owner>/<repo>` URLs are accepted
- Path traversal and `.git` internal access are blocked
- Secret values (API keys, tokens) are **redacted** from all logs and reports
- Temp directory is always cleaned up after the run

---

## ⚠️ Known Limitations

- Shallow clone (last 100 commits only)
- Remote branch protection rules cannot be inferred from local files
- LLM may occasionally produce false positives or miss some issues
- Free-tier API keys (Groq) have token-per-minute limits — the agent handles this automatically with retries

---

## 📋 CLI Options

```bash
python agent.py --repo <github_url> --output <report.json> [options]

Options:
  --repo          Public GitHub repository URL (required)
  --output        Path to write the JSON report (required)
  --max-steps     Maximum LLM tool-call iterations (default: 25)
  --trace         Tool-call trace file path (default: <output>.trace.jsonl)
```
