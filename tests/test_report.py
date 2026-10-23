import json

from report import build_report, validate_violations, write_report


def v(**kw):
    base = dict(rule_id="SEC-001", category="security", severity="high", file="a.py", line=1,
                description="d", evidence="e", recommendation="r")
    base.update(kw)
    return base


def test_valid_item_kept_and_sorted(tmp_path):
    (tmp_path / "a.py").write_text("x=1\n")
    out = validate_violations([v(severity="low", rule_id="B-1", description="x"), v()], tmp_path, log=lambda *_: None)
    assert [o["severity"] for o in out] == ["high", "low"]


def test_invalid_severity_and_missing_field_dropped(tmp_path):
    out = validate_violations([v(severity="urgent"), v(evidence="")], tmp_path, log=lambda *_: None)
    assert out == []


def test_nonexistent_file_and_bad_line_become_null(tmp_path):
    (tmp_path / "a.py").write_text("x=1\n")
    out = validate_violations([v(file="ghost.py", line=5), v(rule_id="X-1", description="y", line=99)],
                              tmp_path, log=lambda *_: None)
    assert out[0]["file"] is None and out[0]["line"] is None or out[1]["file"] is None
    assert all(o["line"] is None for o in out)


def test_duplicates_removed(tmp_path):
    (tmp_path / "a.py").write_text("x=1\n")
    out = validate_violations([v(), v(rule_id="SEC-002")], tmp_path, log=lambda *_: None)
    assert len(out) == 1


def test_secret_in_text_is_redacted(tmp_path):
    out = validate_violations([v(file=None, line=None, evidence="password = hunter2pass")], tmp_path, log=lambda *_: None)
    assert "hunter2pass" not in out[0]["evidence"]


def test_write_report_valid_json_with_empty_list(tmp_path):
    p = tmp_path / "out" / "r.json"
    write_report(str(p), build_report("https://github.com/a/b", []))
    assert json.loads(p.read_text()) == {"repository": "https://github.com/a/b", "violations": []}
