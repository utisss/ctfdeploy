import re
from collections.abc import Iterator
from datetime import datetime
from itertools import pairwise
from pathlib import Path

import yaml

from ctfdeploy.compose import check_compose, published_ports
from ctfdeploy.model import Challenge, Event, Repo
from ctfdeploy.problem import Problem
from ctfdeploy.schedule import desired_events
from ctfdeploy.yamldoc import YamlDoc

REQUIRED = ("name", "category", "description", "flags")


def check(repo: Repo, now: datetime) -> list[Problem]:
    """Problems in the events that are up now or still to come."""
    events = checked_events(repo, now)
    problems = [p for e in events for c in e.challenges for p in check_challenge(repo, c)]
    problems += [p for e in events for p in _check_event(e)]
    problems += [p for a, b in pairwise(events) for p in _check_overlap(a, b)]
    return problems


def checked_events(repo: Repo, now: datetime) -> list[Event]:
    up = {e for e, _ in desired_events(repo.events, now).events}
    return [e for e in repo.events if e in up or e.end is None or e.end > now]


def check_challenge(repo: Repo, challenge: Challenge) -> Iterator[Problem]:
    doc = challenge.doc
    spec = doc.data if isinstance(doc.data, dict) else {}

    def problem(message: str, *keys: str) -> Problem:
        return Problem(doc.path, doc.line(*keys), message)

    for key in REQUIRED:
        if not spec.get(key):
            yield problem(f"{key} is required")
    if "state" in spec:
        yield problem("remove state: ctfdeploy shows challenges when their event starts", "state")
    if repo.categories and spec.get("category") not in repo.categories:
        yield problem(f"category must be one of {', '.join(repo.categories)}", "category")
    yield from _check_scoring(spec, problem)
    for i, name in enumerate(spec.get("files") or []):
        if not (challenge.path / name).is_file():
            yield problem(f"file {name} does not exist", "files", i)
    if challenge.compose:
        yield from _check_hosting(challenge, problem)


def _check_scoring(spec: dict, problem) -> Iterator[Problem]:
    kind = spec.get("type", "standard")
    if kind == "standard" and not isinstance(spec.get("value"), int):
        yield problem("value must be a number", "value")
    elif kind == "dynamic":
        missing = {"initial", "decay", "minimum"} - set(spec.get("extra") or {})
        if missing:
            yield problem(f"dynamic scoring needs extra: {', '.join(sorted(missing))}", "extra")
    elif kind not in ("standard", "dynamic"):
        yield problem("type must be standard or dynamic", "type")


def _check_hosting(challenge: Challenge, problem) -> Iterator[Problem]:
    try:
        compose = YamlDoc.load(challenge.path / challenge.compose)
    except yaml.YAMLError as e:
        yield Problem(challenge.path / challenge.compose, 1, f"invalid YAML: {e}")
        return
    yield from check_compose(compose)
    published = {port for port, _ in published_ports(compose)}
    port = connection_port(challenge.spec.get("connection_info"))
    if port is not None and port not in published:
        yield problem(f"connection_info port {port} is not published", "connection_info")


def connection_port(connection_info) -> int | None:
    """The port in `nc HOST PORT` or `scheme://HOST:PORT/...`."""
    match = re.match(r"\s*(?:nc\s+\S+\s+|\w+://[^/:]+:)(\d+)", str(connection_info or ""))
    return int(match.group(1)) if match else None


def _check_event(event: Event) -> Iterator[Problem]:
    names = {c.name for c in event.challenges}
    seen: dict[str, Challenge] = {}
    for c in event.challenges:
        if c.name in seen:
            yield Problem(c.doc.path, c.doc.line("name"), f"{seen[c.name].dir} has the same name")
        seen[c.name] = c
        next_name = c.spec.get("next")
        if next_name and next_name not in names:
            yield Problem(c.doc.path, c.doc.line("next"), f"no challenge named {next_name}")
    yield from _check_ports(event.challenges)


def _check_overlap(a: Event, b: Event) -> Iterator[Problem]:
    """Consecutive events are up together, except for challenges the second one reuses."""
    reused = {c.slug for c in b.challenges}
    earlier = {port: c for c in a.challenges if c.slug not in reused for port, _, _ in _ports(c)}
    for c in b.challenges:
        for port, line, path in _ports(c):
            if port in earlier:
                yield Problem(path, line, f"port {port} is also used by {earlier[port].dir}")


def _check_ports(challenges) -> Iterator[Problem]:
    owner: dict[int, Challenge] = {}
    for c in challenges:
        for port, line, path in _ports(c):
            if port in owner and owner[port] is not c:
                yield Problem(path, line, f"port {port} is also used by {owner[port].dir}")
            owner.setdefault(port, c)


def _ports(challenge: Challenge) -> list[tuple[int, int, Path]]:
    if not challenge.compose:
        return []
    try:
        compose = YamlDoc.load(challenge.path / challenge.compose)
    except yaml.YAMLError:
        return []
    return [(port, line, compose.path) for port, line in published_ports(compose)]
