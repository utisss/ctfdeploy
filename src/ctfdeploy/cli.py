import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from ctfdeploy.check import check
from ctfdeploy.model import RepoError, load_repo
from ctfdeploy.problem import Problem
from ctfdeploy.report import Report

FATAL = 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ctfdeploy")
    parser.add_argument("--github", action="store_true", default=None, help="GitHub Actions output")
    commands = parser.add_subparsers(dest="command", required=True)

    run_check = commands.add_parser("check", help="check the events that are up or to come")
    run_check.add_argument("repo", nargs="?", type=Path, default=Path.cwd())
    run_check.set_defaults(func=cmd_check)

    args = parser.parse_args(argv)
    report = Report(args.repo.resolve(), args.github)
    try:
        return args.func(args.repo.resolve(), report)
    except RepoError as e:
        report.problem(Problem(e.path, e.line, str(e)))
        return FATAL


def cmd_check(root: Path, report: Report) -> int:
    problems = check(load_repo(root), datetime.now(UTC))
    for problem in problems:
        report.problem(problem)
    print(f"{len(problems)} problem(s)" if problems else "no problems", file=sys.stderr)
    return 1 if problems else 0
