"""Read-only inspection tools for the DevOps auditing agent.

Nothing here executes code from the inspected repository. All output is passed
through redact() so secret values never reach the LLM, logs or the report.
"""
import fnmatch
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

GITHUB_URL_RE = re.compile(
    r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)

MAX_FILE_BYTES = 200_000
MAX_READ_LINES = 400
COLLAPSE_DIRS = {
    "node_modules", "venv", ".venv", "__pycache__", ".pytest_cache",
    ".mypy_cache", "dist", "build",
}
SEARCH_SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__"}

# ---------------------------------------------------------------- redaction
_KV_RE = re.compile(
    r"""(?ix)
    ((?:password|passwd|pwd|secret|token|api[_-]?key|apikey|access[_-]?key|
        private[_-]?key|auth)[\w\-]*\s*[:=]\s*)
    (["']?)
    ([^\s"',;#]{4,})
    """
)
_PREFIX_RE = re.compile(
    r"\b(AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_\-]{20,}"
    r"|AIza[0-9A-Za-z_\-]{30,}|xox[baprs]-[A-Za-z0-9\-]{10,})\b"
)
_URL_CRED_RE = re.compile(r"(https?://[^/\s:@]+:)[^@\s]+@")
_REFERENCE_HINTS = ("os.", "${", "$", "env", "getenv", "process.", "secrets.", "vars.", "config", "<")


def _kv_sub(m):
    value = m.group(3)
    if value.lower().startswith(_REFERENCE_HINTS):
        return m.group(0)  # reads from env/secret store, not a literal value
    return f"{m.group(1)}{m.group(2)}[REDACTED]"


def redact(text):
    """Mask literal secret values (keeps references such as os.getenv / secrets.X)."""
    if not isinstance(text, str):
        return text
    text = _URL_CRED_RE.sub(r"\1[REDACTED]@", text)
    text = _PREFIX_RE.sub("[REDACTED]", text)
    return _KV_RE.sub(_kv_sub, text)


# ------------------------------------------------------------------ cloning
def validate_repo_url(url):
    m = GITHUB_URL_RE.match((url or "").strip())
    if not m:
        raise ValueError(
            "Invalid repository URL. Expected a public GitHub URL like "
            "https://github.com/owner/repo"
        )
    return f"https://github.com/{m.group(1)}/{m.group(2)}"


def clone_repository(url, timeout=120):
    """Shallow-clone a public GitHub repo into a temp dir. Returns the repo Path."""
    clean = validate_repo_url(url)
    workdir = tempfile.mkdtemp(prefix="audit_")
    target = os.path.join(workdir, "repo")
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        subprocess.run(
            ["git", "clone", "--depth", "100", "--no-tags", "--quiet", "--", clean, target],
            check=True, capture_output=True, text=True, timeout=timeout, env=env,
        )
    except FileNotFoundError:
        shutil.rmtree(workdir, ignore_errors=True)
        raise RuntimeError("git is not installed or not on PATH")
    except subprocess.TimeoutExpired:
        shutil.rmtree(workdir, ignore_errors=True)
        raise RuntimeError(f"git clone timed out after {timeout}s")
    except subprocess.CalledProcessError as e:
        shutil.rmtree(workdir, ignore_errors=True)
        msg = redact((e.stderr or "").strip())[:300]
        raise RuntimeError(f"git clone failed (private/nonexistent repo?): {msg}")
    return Path(target)


def cleanup_repo(repo_path):
    if repo_path is not None:
        shutil.rmtree(Path(repo_path).parent, ignore_errors=True)


# -------------------------------------------------------------------- tools
class ToolError(Exception):
    pass


class RepoTools:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def _resolve(self, path):
        if not path or not isinstance(path, str) or "\x00" in path:
            raise ToolError("a valid relative path is required")
        p = (self.root / path).resolve()
        try:
            rel = p.relative_to(self.root)
        except ValueError:
            raise ToolError("path escapes the repository; access denied")
        if rel.parts and rel.parts[0] == ".git":
            raise ToolError("access to .git internals is not allowed (use git_log)")
        return p

    # -- list_files
    def list_files(self, max_entries=400):
        max_entries = min(int(max_entries or 400), 800)
        lines, count = [], 0
        for dirpath, dirs, files in os.walk(self.root):
            dirs[:] = sorted(d for d in dirs if d != ".git")
            rel_dir = os.path.relpath(dirpath, self.root)
            rel_dir = "" if rel_dir == "." else rel_dir
            for d in list(dirs):
                if d in COLLAPSE_DIRS:
                    dirs.remove(d)
                    n = sum(len(f) for _, _, f in os.walk(os.path.join(dirpath, d)))
                    lines.append(f"{os.path.join(rel_dir, d)}/  [COLLAPSED: {n} files present in checkout]")
            for f in sorted(files):
                if count >= max_entries:
                    lines.append(f"... truncated at {max_entries} entries")
                    return self._list_header() + "\n".join(lines)
                full = os.path.join(dirpath, f)
                try:
                    size = os.lstat(full).st_size
                except OSError:
                    size = -1
                lines.append(f"{os.path.join(rel_dir, f)}  ({size} bytes)")
                count += 1
        return self._list_header() + "\n".join(lines)

    @staticmethod
    def _list_header():
        return "Files present in the repository checkout (i.e. committed to git):\n"

    # -- read_file
    def read_file(self, path, start_line=1, max_lines=300):
        p = self._resolve(path)
        if not p.exists():
            return f"ERROR: file not found: {path}"
        if p.is_dir():
            return f"ERROR: {path} is a directory; use list_files"
        with open(p, "rb") as fh:
            data = fh.read(MAX_FILE_BYTES + 1)
        if b"\0" in data[:4000]:
            return f"[{path}] binary file, content not shown"
        truncated = len(data) > MAX_FILE_BYTES
        text = data[:MAX_FILE_BYTES].decode("utf-8", errors="replace")
        lines = text.splitlines()
        start = max(1, int(start_line or 1))
        n = min(int(max_lines or 300), MAX_READ_LINES)
        chunk = lines[start - 1:start - 1 + n]
        body = "\n".join(f"{start + i}: {redact(l)[:500]}" for i, l in enumerate(chunk))
        end = start + len(chunk) - 1
        note = " (file truncated at 200KB)" if truncated else ""
        if not chunk:
            return f"[{path}] empty or start_line beyond end (total lines: {len(lines)})"
        return f"[{path}] lines {start}-{end} of {len(lines)}{note}\n{body}"

    # -- search_code
    def search_code(self, pattern, file_glob=None, max_results=40):
        try:
            rx = re.compile(pattern, re.IGNORECASE)
        except (re.error, TypeError) as e:
            return f"ERROR: invalid regex: {e}"
        max_results = min(int(max_results or 40), 100)
        deadline = time.time() + 15
        hits = []
        for dirpath, dirs, files in os.walk(self.root):
            dirs[:] = sorted(d for d in dirs if d not in SEARCH_SKIP_DIRS)
            for f in sorted(files):
                full = os.path.join(dirpath, f)
                rel = os.path.relpath(full, self.root)
                if os.path.islink(full):
                    continue
                if file_glob and not (fnmatch.fnmatch(rel, file_glob) or fnmatch.fnmatch(f, file_glob)):
                    continue
                try:
                    if os.path.getsize(full) > MAX_FILE_BYTES:
                        continue
                    with open(full, "rb") as fh:
                        data = fh.read()
                except OSError:
                    continue
                if b"\0" in data[:2000]:
                    continue
                for i, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
                    if rx.search(line[:2000]):
                        hits.append(f"{rel}:{i}: {redact(line.strip())[:200]}")
                        if len(hits) >= max_results:
                            return f"{len(hits)}+ matches (stopped at limit):\n" + "\n".join(hits)
                if time.time() > deadline:
                    return "Search timed out; partial matches:\n" + "\n".join(hits)
        if not hits:
            return f"No matches for pattern: {pattern}"
        return f"{len(hits)} matches:\n" + "\n".join(hits)

    # -- git_log
    def git_log(self, max_count=30, with_files=False):
        n = min(int(max_count or 30), 100)
        cmd = ["git", "-C", str(self.root), "log", f"-n{n}", "--date=short",
               "--pretty=format:%h | %ad | %an | %s"]
        if with_files:
            cmd.append("--name-status")
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            return f"ERROR: git log failed: {e}"
        if r.returncode != 0:
            return f"ERROR: git log failed: {redact(r.stderr.strip())[:200]}"
        return ("(clone is shallow: only the latest 100 commits are available)\n"
                + redact(r.stdout))

    # -- dispatcher
    def execute(self, name, args):
        fn = {
            "list_files": self.list_files,
            "read_file": self.read_file,
            "search_code": self.search_code,
            "git_log": self.git_log,
        }.get(name)
        if fn is None:
            return f"ERROR: unknown tool '{name}'. Available: list_files, read_file, search_code, git_log, submit_report"
        try:
            return fn(**(args or {}))
        except ToolError as e:
            return f"ERROR: {e}"
        except TypeError as e:
            return f"ERROR: bad arguments for {name}: {e}"
        except Exception as e:  # never crash the agent loop because of a tool
            return f"ERROR: {name} failed unexpectedly: {redact(str(e))[:200]}"


# ------------------------------------------------- tool schemas (for the LLM)
_CATEGORIES = ["repository_hygiene", "dependency_management", "code_quality", "testing",
               "ci_cd", "security", "configuration", "collaboration", "build_release"]

TOOL_SPECS = [
    {"name": "list_files",
     "description": "List all files in the repository checkout with sizes. Start here."},
    {"name": "read_file",
     "description": "Read a file with line numbers. Use start_line/max_lines for big files.",
     "parameters": {"type": "OBJECT", "properties": {
         "path": {"type": "STRING", "description": "Relative path, e.g. .github/workflows/ci.yml"},
         "start_line": {"type": "INTEGER"},
         "max_lines": {"type": "INTEGER"}}, "required": ["path"]}},
    {"name": "search_code",
     "description": "Case-insensitive regex search across text files. Returns file:line: text.",
     "parameters": {"type": "OBJECT", "properties": {
         "pattern": {"type": "STRING", "description": "Regex, e.g. (password|secret|api_key)\\s*="},
         "file_glob": {"type": "STRING", "description": "Optional filter like *.py or *.yml"},
         "max_results": {"type": "INTEGER"}}, "required": ["pattern"]}},
    {"name": "git_log",
     "description": "Show recent commit history (hash | date | author | message).",
     "parameters": {"type": "OBJECT", "properties": {
         "max_count": {"type": "INTEGER"},
         "with_files": {"type": "BOOLEAN", "description": "Also list changed files per commit"}}}},
    {"name": "submit_report",
     "description": "FINAL step. Submit all confirmed violations (may be an empty list).",
     "parameters": {"type": "OBJECT", "properties": {
         "violations": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
             "rule_id": {"type": "STRING", "description": "e.g. SEC-001"},
             "category": {"type": "STRING", "enum": _CATEGORIES},
             "severity": {"type": "STRING", "enum": ["critical", "high", "medium", "low"]},
             "file": {"type": "STRING", "nullable": True},
             "line": {"type": "INTEGER", "nullable": True},
             "description": {"type": "STRING"},
             "evidence": {"type": "STRING"},
             "recommendation": {"type": "STRING"}},
             "required": ["rule_id", "category", "severity", "description",
                          "evidence", "recommendation"]}}},
         "required": ["violations"]}},
]
