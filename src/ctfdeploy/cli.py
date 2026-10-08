import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from ctfdeploy import docker
from ctfdeploy.changed import changed_hosted
from ctfdeploy.check import check
from ctfdeploy.ctfd import Ctfd
from ctfdeploy.docker import StepFailed
from ctfdeploy.model import CHALLENGE_FILE, META_FILE, Challenge, RepoError, load_repo
from ctfdeploy.problem import Problem
from ctfdeploy.reconcile import reconcile
from ctfdeploy.report import Report
from ctfdeploy.solve import solve

FATAL = 2

PHASES = {
    "build": lambda c: docker.build(c, c.version()),
    "up": lambda c: docker.deploy(c, c.version()),
    "probe": docker.probe,
    "solve": solve,
    "logs": docker.logs,
}


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    path = args.path.resolve()
    report = Report(_repo_root(path) or path, args.github)
    try:
        return args.func(args, path, report)
    except RepoError as e:
        report.problem(Problem(e.path, e.line, str(e)))
        return FATAL


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ctfdeploy")
    parser.add_argument("--github", action="store_true", default=None, help="GitHub Actions output")
    commands = parser.add_subparsers(dest="command", required=True)

    sub = commands.add_parser("check", help="check the events that are up or to come")
    sub.add_argument("path", nargs="?", type=Path, default=Path.cwd(), metavar="REPO")
    sub.set_defaults(func=cmd_check)

    sub = commands.add_parser("changed", help="hosted challenges that differ from BASE, as JSON")
    sub.add_argument("base")
    sub.add_argument("path", nargs="?", type=Path, default=Path.cwd(), metavar="REPO")
    sub.set_defaults(func=cmd_changed)

    sub = commands.add_parser("reconcile", help="make the host and CTFd match the repo")
    sub.add_argument("--fetch", action="store_true", help="reset to the upstream branch first")
    sub.add_argument("path", nargs="?", type=Path, default=Path.cwd(), metavar="REPO")
    sub.set_defaults(func=cmd_reconcile)

    for phase, text in [
        ("build", "build a challenge's images"),
        ("up", "deploy a challenge's stack and wait until it is healthy"),
        ("probe", "connect to each port a challenge publishes"),
        ("solve", "run a challenge's solve.py against it"),
        ("logs", "print a deployed challenge's tasks and service logs"),
    ]:
        sub = commands.add_parser(phase, help=text)
        sub.add_argument("path", type=Path, metavar="DIR")
        sub.set_defaults(func=cmd_phase)
    return parser


def cmd_check(args, root: Path, report: Report) -> int:
    problems = check(load_repo(root), datetime.now(UTC))
    for problem in problems:
        report.problem(problem)
    print(f"{len(problems)} problem(s)" if problems else "no problems", file=sys.stderr)
    return 1 if problems else 0


def cmd_changed(args, root: Path, report: Report) -> int:
    changed = changed_hosted(load_repo(root), datetime.now(UTC), args.base)
    print(json.dumps([c.dir.as_posix() for c in changed]))
    return 0


def cmd_reconcile(args, root: Path, report: Report) -> int:
    if "CTFD_TOKEN" not in os.environ:
        report.error("CTFD_TOKEN is not set", title="reconcile")
        return FATAL
    ctfd = Ctfd(os.environ.get("CTFD_URL", "unix:/run/ctfd/ctfd.sock"), os.environ["CTFD_TOKEN"])
    try:
        return reconcile(root, ctfd, report, args.fetch)
    except StepFailed as e:
        report.error(str(e), title="reconcile")
        return FATAL


def cmd_phase(args, path: Path, report: Report) -> int:
    challenge = _find_challenge(path)
    title = f"{challenge.slug}: {args.command}"
    if args.command != "solve" and not challenge.compose:
        report.error("the challenge has no compose file", title)
        return 1
    try:
        print(PHASES[args.command](challenge))
        return 0
    except StepFailed as e:
        print(e.log)
        report.error(str(e), title)
        return 1


def _repo_root(path: Path) -> Path | None:
    return next((p for p in (path, *path.parents) if (p / META_FILE).is_file()), None)


def _find_challenge(path: Path) -> Challenge:
    root = _repo_root(path)
    if root is None:
        raise RepoError(path, 1, f"not inside a challenge repo: no {META_FILE} above it")
    for event in load_repo(root).events:
        for challenge in event.challenges:
            if challenge.path.resolve() == path:
                return challenge
    raise RepoError(path / CHALLENGE_FILE, 1, f"not a challenge of any event in {META_FILE}")
