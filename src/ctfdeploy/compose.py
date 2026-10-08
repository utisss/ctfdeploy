import os
import re
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

from ctfdeploy.problem import Problem
from ctfdeploy.yamldoc import YamlDoc

DROPPED_BY_SWARM = {
    "restart": "swarm ignores it; use deploy.restart_policy",
    "container_name": "swarm names containers itself",
    "tmpfs": "swarm ignores it; use a volumes entry with type: tmpfs",
    "mem_limit": "swarm ignores it; use deploy.resources.limits.memory",
    "cpus": "swarm ignores it; use deploy.resources.limits.cpus",
    "network_mode": "swarm services cannot share another network namespace",
    "security_opt": "swarm ignores it",
    "extends": "challenges must be self-contained",
}
UNSAFE = ("privileged", "cap_add", "devices", "pid", "ipc", "userns_mode")
RESERVED_PORTS = {22, 80, 443, 2377, 7946, 4789}


def check_compose(doc: YamlDoc) -> Iterator[Problem]:
    services = doc.data.get("services") if isinstance(doc.data, dict) else None
    if not isinstance(services, dict) or not services:
        yield Problem(doc.path, 1, "compose file has no services")
        return
    for name, service in services.items():
        yield from _check_service(doc, name, service or {})
    yield from _stack_config(doc.path)


def published_ports(doc: YamlDoc) -> list[tuple[int, int]]:
    """Each fixed host port the compose file publishes, with its line."""
    ports = []
    for name, service in (doc.data.get("services") or {}).items():
        for i, entry in enumerate((service or {}).get("ports") or []):
            port = _published(entry)
            if port is not None:
                ports.append((port, doc.line("services", name, "ports", i)))
    return ports


def _check_service(doc: YamlDoc, name: str, service: dict) -> Iterator[Problem]:
    def problem(key: str | int, message: str, *more: str | int) -> Problem:
        return Problem(doc.path, doc.line("services", name, key, *more), f"{name}: {message}")

    if "build" in service and "${TAG" not in str(service.get("image", "")):
        yield problem("build", "built services need image: <name>:${TAG:-dev}")
    for key, why in DROPPED_BY_SWARM.items():
        if key in service:
            yield problem(key, f"{key}: {why}")
    for key in UNSAFE:
        if key in service:
            yield problem(key, f"{key} is not allowed")
    for i, volume in enumerate(service.get("volumes") or []):
        if _bind_escapes(volume, doc.path.parent):
            yield problem("volumes", "bind mounts must stay inside the challenge directory", i)
    for i, entry in enumerate(service.get("ports") or []):
        port = _published(entry)
        if port is None:
            yield problem("ports", f"publish a fixed host port, e.g. 7001:{entry}", i)
        elif port in RESERVED_PORTS:
            yield problem("ports", f"port {port} belongs to the host", i)


def _published(entry) -> int | None:
    if isinstance(entry, dict):
        published = entry.get("published")
        return int(published) if str(published).isdigit() else None
    parts = str(entry).split("/")[0].split(":")
    return int(parts[-2]) if len(parts) >= 2 and parts[-2].isdigit() else None


def _bind_escapes(volume, base: Path) -> bool:
    if isinstance(volume, dict):
        source = volume.get("source", "") if volume.get("type") == "bind" else None
    else:
        source = str(volume).split(":")[0] if ":" in str(volume) else None
    if source is None or not re.match(r"[./~]", source):
        return False
    return source.startswith("~") or not (base / source).resolve().is_relative_to(base.resolve())


def _stack_config(path: Path) -> Iterator[Problem]:
    if shutil.which("docker") is None:
        return
    result = subprocess.run(
        ["docker", "stack", "config", "-c", path.name],
        cwd=path.parent,
        env={**os.environ, "TAG": "check"},
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        yield Problem(path, 1, f"docker stack config: {result.stderr.strip()}")
