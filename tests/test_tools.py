import subprocess

import pytest

from tools import RepoTools, redact, validate_repo_url


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "README.md").write_text("# demo\n")
    (tmp_path / "config.py").write_text('API_KEY = "abcd1234efgh5678"\nDB = os.getenv("DB_URL")\n')
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text("permissions: write-all\n")
    (tmp_path / "node_modules" / "x").mkdir(parents=True)
    (tmp_path / "node_modules" / "x" / "a.js").write_text("1")
    return tmp_path


def test_list_files_collapses_noise(repo):
    out = RepoTools(repo).list_files()
    assert "README.md" in out and ".github/workflows/ci.yml" in out
    assert "node_modules/  [COLLAPSED" in out


def test_read_file_numbers_lines_and_redacts(repo):
    out = RepoTools(repo).read_file("config.py")
    assert "1: " in out and "abcd1234efgh5678" not in out and "[REDACTED]" in out
    assert "os.getenv" in out  # env reference is kept


def test_read_file_blocks_path_traversal(repo):
    t = RepoTools(repo)
    assert t.execute("read_file", {"path": "../../etc/passwd"}).startswith("ERROR")
    assert t.execute("read_file", {"path": "/etc/passwd"}).startswith("ERROR")


def test_read_missing_file(repo):
    assert "not found" in RepoTools(repo).read_file("nope.txt")


def test_search_code_finds_and_redacts(repo):
    out = RepoTools(repo).search_code(r"api_key\s*=")
    assert "config.py:1" in out and "abcd1234efgh5678" not in out


def test_search_invalid_regex(repo):
    assert RepoTools(repo).search_code("([").startswith("ERROR")


def test_unknown_tool_and_bad_args(repo):
    t = RepoTools(repo)
    assert t.execute("rm_rf", {}).startswith("ERROR")
    assert t.execute("read_file", {"bogus": 1}).startswith("ERROR")


def test_git_log(repo):
    def g(*a):
        subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
    try:
        g("init", "-q")
        g("-c", "user.name=t", "-c", "user.email=t@t", "add", ".")
        g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "initial commit")
    except (FileNotFoundError, subprocess.CalledProcessError):
        pytest.skip("git unavailable")
    assert "initial commit" in RepoTools(repo).git_log()


def test_redact_patterns():
    assert "ghp_" not in redact("token ghp_" + "a" * 30)
    assert "hunter2pass" not in redact("password: hunter2pass")
    assert redact("password: ${{ secrets.DB }}") == "password: ${{ secrets.DB }}"
    assert "s3cret" not in redact("https://user:s3cretpw@host/x")


def test_url_validation():
    assert validate_repo_url("https://github.com/a/b.git/") == "https://github.com/a/b"
    for bad in ["http://github.com/a/b", "https://evil.com/a/b", "file:///etc", "https://github.com/a"]:
        with pytest.raises(ValueError):
            validate_repo_url(bad)
