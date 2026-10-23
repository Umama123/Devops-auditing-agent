"""Validate, sanitise and write the JSON report."""
import json
import os
import re
from pathlib import Path

from tools import redact

SEVERITIES = ("critical", "high", "medium", "low")
REQUIRED = ("rule_id", "category", "severity", "description", "evidence", "recommendation")


def _norm(s):
    return re.sub(r"\W+", " ", s.lower()).strip()


def validate_violations(raw, root=None, log=print):
    clean, seen_keys, seen_desc = [], set(), set()
    for i, v in enumerate(raw or []):
        if not isinstance(v, dict):
            log(f"  dropped finding #{i}: not an object")
            continue
        if any(not isinstance(v.get(k), str) or not v.get(k).strip() for k in REQUIRED):
            log(f"  dropped finding #{i}: missing required field")
            continue
        sev = v["severity"].strip().lower()
        if sev not in SEVERITIES:
            log(f"  dropped finding #{i}: invalid severity '{v['severity']}'")
            continue

        file = v.get("file")
        file = file.strip() if isinstance(file, str) and file.strip() else None
        line = v.get("line")
        try:
            line = int(line) if line is not None and int(line) > 0 else None
        except (TypeError, ValueError):
            line = None

        if file and root is not None:
            p = Path(root) / file
            if not p.is_file():
                file, line = None, None  # cannot cite a file that does not exist
            elif line is not None:
                try:
                    n = len(p.read_text(encoding="utf-8", errors="replace").splitlines())
                    line = line if line <= n else None
                except OSError:
                    line = None
        if file is None:
            line = None

        item = {
            "rule_id": redact(v["rule_id"].strip()),
            "category": v["category"].strip().lower(),
            "severity": sev,
            "file": file,
            "line": line,
            "description": redact(v["description"].strip()),
            "evidence": redact(v["evidence"].strip()),
            "recommendation": redact(v["recommendation"].strip()),
        }
        key = (item["rule_id"], file, line)
        dkey = (item["category"], file, _norm(item["description"]))
        if key in seen_keys or dkey in seen_desc:
            log(f"  dropped duplicate finding: {item['rule_id']}")
            continue
        seen_keys.add(key)
        seen_desc.add(dkey)
        clean.append(item)
    clean.sort(key=lambda x: (SEVERITIES.index(x["severity"]), x["rule_id"]))
    return clean


def build_report(repo_url, violations):
    return {"repository": repo_url, "violations": violations}


def write_report(path, report):
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)
