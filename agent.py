"""CLI entry point:  python agent.py --repo <github_url> --output report.json"""
import argparse
import os
import sys

from dotenv import load_dotenv

from agent_loop import run_agent
from prompts import SYSTEM_PROMPT
from report import build_report, validate_violations, write_report
from tools import TOOL_SPECS, RepoTools, cleanup_repo, clone_repository, redact, validate_repo_url


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="AI agent that audits a GitHub repo for DevOps issues.")
    p.add_argument("--repo", required=True, help="Public GitHub repository URL")
    p.add_argument("--output", required=True, help="Path of the JSON report to write")
    p.add_argument("--max-steps", type=int, default=25, help="Max tool-call iterations")
    p.add_argument("--trace", default=None, help="Tool-call trace file (default: <output>.trace.jsonl)")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    load_dotenv()

    try:
        repo_url = validate_repo_url(args.repo)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    try:  # build the LLM client first: fail fast on config problems, before cloning
        from llm import create_client
        llm = create_client(SYSTEM_PROMPT, TOOL_SPECS)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    repo_path = None
    try:
        print(f"=== Auditing {repo_url} (LLM {llm.describe()}) ===")
        print("Cloning repository (read-only, temp directory)...")
        repo_path = clone_repository(repo_url)
        tools = RepoTools(repo_path)
        trace = args.trace or f"{args.output}.trace.jsonl"
        raw = run_agent(repo_url, tools, llm, max_steps=args.max_steps, trace_path=trace)
        violations = validate_violations(raw, repo_path)
        write_report(args.output, build_report(repo_url, violations))
        print(f"Done. {len(violations)} violation(s) written to {args.output}")
        return 0
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"Fatal error: {redact(str(e))}", file=sys.stderr)
        return 1
    finally:
        cleanup_repo(repo_path)


if __name__ == "__main__":
    sys.exit(main())
