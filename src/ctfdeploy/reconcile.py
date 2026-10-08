import fcntl
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ctfdeploy import docker
from ctfdeploy.check import check
from ctfdeploy.ctfd import Ctfd
from ctfdeploy.docker import StepFailed
from ctfdeploy.model import CHALLENGE_FILE, Challenge, git, load_repo
from ctfdeploy.notify import notify_changes
from ctfdeploy.problem import Problem
from ctfdeploy.report import Report
from ctfdeploy.schedule import desired_events
from ctfdeploy.sync import link_next, sync_challenge, sync_config

WORKERS = 4


@dataclass
class Outcome:
    challenge: Challenge
    stack: str
    ctfd: str = ""
    cid: int | None = None
    next_id: int | None = None
    phase: str = ""
    problems: list[Problem] = field(default_factory=list)
    log: list[str] = field(default_factory=list)

    @property
    def failure(self) -> str:
        return self.problems[0].message if self.problems else ""


def reconcile(root: Path, ctfd: Ctfd, report: Report, fetch: bool) -> int:
    with _lock(root):
        if fetch:
            git(root, "fetch", "--quiet")
            git(root, "reset", "--quiet", "--hard", "@{upstream}")
        repo = load_repo(root)
        now = datetime.now(UTC)
        desired = desired_events(repo.events, now)
        problems = check(repo, now)
        wanted = _wanted(desired.events)
        ids = {c["name"]: c["id"] for c in ctfd.get("/challenges?view=admin")}
        deployed = docker.deployed_tags()
        failures: dict[str, str] = {}

        hosted = {c.stack for c, _ in wanted if c.compose}
        for stack in sorted(deployed.keys() - hosted):
            with _step(report, failures, stack):
                report.group(f"✓ {stack}  removed", docker.remove(stack))

        def one(item: tuple[Challenge, bool]) -> Outcome:
            challenge, visible = item
            print(f"{challenge.slug}: reconciling\n", end="", flush=True)
            return _reconcile_one(
                ctfd,
                challenge,
                visible,
                deployed.get(challenge.stack),
                ids.get(challenge.name),
                [p for p in problems if p.path.is_relative_to(challenge.path)],
            )

        outcomes = []
        with ThreadPoolExecutor(WORKERS) as pool:
            for outcome in pool.map(one, wanted):
                if outcome.cid:
                    ids[outcome.challenge.name] = outcome.cid
                if outcome.failure:
                    failures[outcome.challenge.slug] = outcome.failure
                _report(report, outcome)
                outcomes.append(outcome)
        next_ids = {o.cid: o.next_id for o in outcomes if o.cid}

        in_repo = {c.name for e in repo.events for c in e.challenges}
        for name in sorted((in_repo - {c.name for c, _ in wanted}) & ids.keys()):
            with _step(report, failures, f"ctfd: {name}"):
                ctfd.delete(f"/challenges/{ids[name]}")
                del ids[name]
                report.group(f"✓ ctfd: {name}  deleted", "")
        with _step(report, failures, "ctfd: next"):
            report.group(
                "✓ ctfd: next  linked", link_next(ctfd, [c for c, _ in wanted], ids, next_ids)
            )
        with _step(report, failures, "ctfd: config"):
            changed = sync_config(ctfd, repo, desired.config)
            report.group(f"✓ ctfd: config  {desired.config.name}", changed)

    if report.github:
        report.summary(_summary(desired.events, outcomes))
    notify_changes(failures)
    return 1 if failures else 0


def _wanted(events) -> list[tuple[Challenge, bool]]:
    """Every challenge that should be up, once per directory name, as the current event has it.

    A directory reused by the next event shares one stack, so it stays visible while any event
    that is up shows it.
    """
    wanted: dict[str, tuple[Challenge, bool]] = {}
    for event, visible in events:
        for challenge in event.challenges:
            first, shown = wanted.get(challenge.slug, (challenge, False))
            wanted[challenge.slug] = (first, shown or visible)
    return list(wanted.values())


def _reconcile_one(
    ctfd: Ctfd,
    challenge: Challenge,
    visible: bool,
    tags: set[str] | None,
    cid: int | None,
    problems: list[Problem],
) -> Outcome:
    """Deploy then sync one challenge. One that fails `check` is left as it is.

    Runs on a worker thread, so it only reads shared state.
    """
    outcome = Outcome(challenge, stack="—" if challenge.compose else "not hosted")
    if problems:
        outcome.phase, outcome.problems = "check", problems
        return outcome
    try:
        if challenge.compose:
            outcome.phase = "deploy"
            outcome.stack = _deploy(challenge, tags, outcome.log)
        outcome.phase = "ctfd"
        outcome.cid, outcome.ctfd, outcome.next_id = sync_challenge(ctfd, challenge, visible, cid)
    except StepFailed as e:
        file = challenge.compose if outcome.phase == "deploy" else challenge.path / CHALLENGE_FILE
        outcome.problems = [Problem(file, 1, str(e))]
        outcome.log.append(e.log)
    return outcome


def _deploy(challenge: Challenge, tags: set[str] | None, log: list[str]) -> str:
    version = challenge.version()
    if tags == {version}:
        return f"up to date {version}"
    log.append(docker.build(challenge, version))
    log.append(docker.deploy(challenge, version))
    return f"deployed {version}"


def _report(report: Report, outcome: Outcome) -> None:
    slug = outcome.challenge.slug
    for problem in outcome.problems:
        report.problem(problem, title=f"{slug}: {outcome.phase}")
    mark = "✗" if outcome.failure else "✓"
    detail = outcome.failure or f"{outcome.stack}, ctfd {outcome.ctfd}"
    report.group(f"{mark} {slug}  {detail}", "\n".join(outcome.log))


@contextmanager
def _step(report: Report, failures: dict[str, str], key: str):
    """A host-wide step whose failure is recorded without stopping the run."""
    try:
        yield
    except StepFailed as e:
        failures[key] = str(e)
        report.error(str(e), title=key)


def _summary(events, outcomes: list[Outcome]) -> str:
    phases = ", ".join(f"{e.name} ({'visible' if v else 'hidden'})" for e, v in events)
    head = ["| | Challenge | Stack | CTFd | Failure |", "| --- | --- | --- | --- | --- |"]
    rows = [
        f"| {'✗' if o.failure else '✓'} | `{o.challenge.slug}` | {o.stack} | {o.ctfd} "
        f"| {_cell(o.failure)} |"
        for o in outcomes
    ]
    return "\n".join([f"**Events:** {phases}", "", *head, *rows])


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


@contextmanager
def _lock(root: Path):
    """One run per repo at a time: a push that lands mid-run waits, then runs on the new commit."""
    fd = os.open(root, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)
