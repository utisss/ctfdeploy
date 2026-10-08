import fcntl
import os
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ctfdeploy import docker
from ctfdeploy.ctfd import Ctfd, CtfdError
from ctfdeploy.docker import StepFailed
from ctfdeploy.model import CHALLENGE_FILE, Challenge, RepoError, load_repo
from ctfdeploy.notify import notify_changes
from ctfdeploy.problem import Problem
from ctfdeploy.report import Report
from ctfdeploy.schedule import desired_events
from ctfdeploy.sync import link_next, sync_challenge, sync_config


@dataclass
class Outcome:
    challenge: Challenge
    stack: str
    ctfd: str = ""
    failure: str = ""
    log: list[str] = field(default_factory=list)


def reconcile(root: Path, ctfd: Ctfd, report: Report, fetch: bool) -> int:
    with _lock(root):
        if fetch:
            _fetch(root)
        repo = load_repo(root)
        desired = desired_events(repo.events, datetime.now(UTC))
        wanted = _wanted(desired.events)
        ids = {c["name"]: c["id"] for c in ctfd.get("/challenges?view=admin")}
        deployed = docker.deployed_tags()
        failures: dict[str, str] = {}

        hosted = {c.stack for c, _ in wanted if c.compose}
        for stack in sorted(deployed.keys() - hosted):
            _step(report, failures, stack, "removed", lambda s=stack: docker.remove(s))

        outcomes = [_reconcile_one(ctfd, c, v, deployed, ids, report) for c, v in wanted]
        failures |= {o.challenge.slug: o.failure for o in outcomes if o.failure}

        in_repo = {c.name for e in repo.events for c in e.challenges}
        for name in sorted((in_repo - {c.name for c, _ in wanted}) & ids.keys()):
            _step(report, failures, f"ctfd: {name}", "deleted",
                  lambda n=name: ctfd.delete(f"/challenges/{ids.pop(n)}"))  # fmt: skip
        _step(report, failures, "ctfd: next", "linked",
              lambda: link_next(ctfd, [c for c, _ in wanted], ids))  # fmt: skip
        _step(report, failures, "ctfd: config", desired.config.name,
              lambda: sync_config(ctfd, repo, desired.config))  # fmt: skip
        _step(report, failures, "images", "pruned", docker.prune_images)

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
    ctfd: Ctfd, challenge: Challenge, visible: bool, deployed: dict, ids: dict, report: Report
) -> Outcome:
    print(f"{challenge.slug}: reconciling", flush=True)
    outcome = Outcome(challenge, stack="—" if challenge.compose else "not hosted")
    phase, file = "deploy", challenge.compose
    try:
        if challenge.compose:
            outcome.stack = _deploy(challenge, deployed.get(challenge.stack), outcome.log)
        phase, file = "ctfd", challenge.path / CHALLENGE_FILE
        ids[challenge.name], outcome.ctfd = sync_challenge(
            ctfd, challenge, visible, ids.get(challenge.name)
        )
    except (StepFailed, CtfdError) as e:
        outcome.failure = str(e)
        outcome.log.append(getattr(e, "log", ""))
        report.problem(Problem(file, 1, outcome.failure), title=f"{challenge.slug}: {phase}")
    mark = "✗" if outcome.failure else "✓"
    detail = outcome.failure or f"{outcome.stack}, ctfd {outcome.ctfd}"
    report.group(f"{mark} {challenge.slug}  {detail}", "\n".join(outcome.log))
    return outcome


def _deploy(challenge: Challenge, deployed: set[str] | None, log: list[str]) -> str:
    version = challenge.version()
    if deployed == {version}:
        return f"up to date {version}"
    log.append(docker.build(challenge, version))
    log.append(docker.deploy(challenge, version))
    return f"deployed {version}"


def _step(report: Report, failures: dict, key: str, done: str, action) -> None:
    try:
        result = action()
        report.group(f"✓ {key}  {done}", result if isinstance(result, str) else "")
    except (StepFailed, CtfdError) as e:
        failures[key] = str(e)
        report.error(str(e), title=key)


def _summary(events, outcomes: list[Outcome]) -> str:
    phases = ", ".join(f"{e.name} ({'visible' if v else 'hidden'})" for e, v in events)
    rows = [
        " | ".join(["", "✗" if o.failure else "✓", f"`{o.challenge.slug}`", o.stack, o.ctfd,
                    o.failure.replace("|", "\\|"), ""]).strip()
        for o in outcomes
    ]  # fmt: skip
    head = ["| | Challenge | Stack | CTFd | Failure |", "| --- | --- | --- | --- | --- |"]
    return "\n".join([f"**Events:** {phases}", "", *head, *rows])


def _fetch(root: Path) -> None:
    for cmd in (["git", "fetch", "--quiet"], ["git", "reset", "--quiet", "--hard", "@{upstream}"]):
        result = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
        if result.returncode != 0:
            raise RepoError(root, 1, f"{' '.join(cmd)}: {result.stderr.strip()}")


@contextmanager
def _lock(root: Path):
    """One run per repo at a time: a push that lands mid-run waits, then runs on the new commit."""
    fd = os.open(root, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)
