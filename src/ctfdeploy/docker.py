import json
import os
import socket
import subprocess
import time

from ctfdeploy.compose import published_ports
from ctfdeploy.model import Challenge
from ctfdeploy.yamldoc import YamlDoc

HEALTHY_TIMEOUT = 120
POLL = 2


class StepFailed(Exception):
    def __init__(self, reason: str, log: str = ""):
        super().__init__(reason)
        self.log = log


def build(challenge: Challenge, tag: str) -> str:
    return _run(
        ["docker", "compose", "-f", challenge.compose.name, "-p", challenge.stack, "build"],
        challenge,
        tag,
        "build failed",
    )


def deploy(challenge: Challenge, tag: str) -> str:
    log = _run(
        ["docker", "stack", "deploy", "--detach=true", "--prune", "--resolve-image", "never"]
        + ["-c", challenge.compose.name, challenge.stack],
        challenge,
        tag,
        "deploy failed",
    )
    return log + wait_healthy(challenge.stack)


def remove(stack: str) -> str:
    return _docker("stack", "rm", stack)


def prune_images() -> str:
    """Remove compose-built images no container uses: on the host, old challenge versions."""
    return _docker(
        "image", "prune", "--all", "--force", "--filter", "label=com.docker.compose.project"
    )  # fmt: skip


def deployed_tags() -> dict[str, set[str]]:
    """The image tags each managed stack is running."""
    ids = _docker("service", "ls", "-q", "--filter", "label=com.docker.stack.namespace").split()
    if not ids:
        return {}
    namespace = '{{index .Spec.Labels "com.docker.stack.namespace"}}'
    image_ref = "{{.Spec.TaskTemplate.ContainerSpec.Image}}"
    out = _docker("service", "inspect", *ids, "--format", f"{namespace} {image_ref}")
    stacks: dict[str, set[str]] = {}
    for line in out.splitlines():
        stack, image = line.split(" ", 1)
        if stack.startswith("chall-"):
            stacks.setdefault(stack, set()).add(image.split("@")[0].rpartition(":")[2])
    return stacks


def wait_healthy(stack: str) -> str:
    """Wait until every service runs all its replicas healthy and no update is in flight."""
    deadline = time.monotonic() + HEALTHY_TIMEOUT
    while True:
        services = _services(stack)
        rolled_back = [s for s in services if s["update"] == "rollback_completed"]
        if rolled_back:
            health = _last_health_output(stack)
            reason = f"{rolled_back[0]['name']}: update rolled back to the previous version"
            raise StepFailed(f"{reason}: {health}" if health else reason, _tasks(stack))
        if services and all(_converged(s) for s in services):
            return _tasks(stack)
        if time.monotonic() > deadline:
            raise StepFailed(_unhealthy_reason(stack), _tasks(stack))
        time.sleep(POLL)


def probe(challenge: Challenge, attempts: int = 10) -> str:
    log = []
    for port, _ in published_ports(YamlDoc.load(challenge.path / challenge.compose)):
        for attempt in range(attempts):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=3).close()
                log.append(f"port {port}: open")
                break
            except OSError as e:
                if attempt == attempts - 1:
                    raise StepFailed(f"port {port}: {e.strerror or e}", "\n".join(log)) from e
                time.sleep(1)
    return "\n".join(log)


def _services(stack: str) -> list[dict]:
    ids = _docker("service", "ls", "-q", "--filter", f"label=com.docker.stack.namespace={stack}")
    if not ids.split():
        return []
    return [
        {
            "name": s["Spec"]["Name"],
            "replicas": s["Spec"]["Mode"].get("Replicated", {}).get("Replicas", 1),
            "update": (s.get("UpdateStatus") or {}).get("State", ""),
        }
        for s in json.loads(_docker("service", "inspect", *ids.split()))
    ]


def _converged(service: dict) -> bool:
    if service["update"] in ("updating", "rollback_started", "paused", "rollback_paused"):
        return False
    running = _docker(
        "service", "ps", service["name"], "--filter", "desired-state=running",
        "--format", "{{.CurrentState}}",
    )  # fmt: skip
    return sum(s.startswith("Running") for s in running.splitlines()) >= service["replicas"]


def _unhealthy_reason(stack: str) -> str:
    errors = _docker(
        "stack", "ps", stack, "--no-trunc", "--format", "{{.Name}}: {{.Error}}",
    ).splitlines()  # fmt: skip
    error = next((e for e in errors if not e.endswith(": ")), "")
    if error:
        return error
    health = _last_health_output(stack)
    if health:
        return f"healthcheck failing: {health}"
    return f"not healthy after {HEALTHY_TIMEOUT}s"


def _last_health_output(stack: str) -> str:
    containers = _docker(
        "ps", "-aq", "--filter", f"label=com.docker.stack.namespace={stack}"
    ).split()  # fmt: skip
    for container in containers:
        health = json.loads(_docker("inspect", "--format", "{{json .State.Health}}", container))
        if health and health.get("Log"):
            last = health["Log"][-1]
            lines = last["Output"].strip().splitlines()
            return lines[-1] if lines else f"exit code {last['ExitCode']}"
    return ""


def _tasks(stack: str) -> str:
    return _docker(
        "stack", "ps", stack, "--no-trunc",
        "--format", "table {{.Name}}\t{{.Image}}\t{{.CurrentState}}\t{{.Error}}",
    )  # fmt: skip


def _run(cmd: list[str], challenge: Challenge, tag: str, failure: str) -> str:
    result = subprocess.run(
        cmd,
        cwd=challenge.path,
        env={**os.environ, "TAG": tag},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    log = "\n".join(line for line in result.stdout.splitlines() if line.strip())
    if result.returncode != 0:
        raise StepFailed(f"{failure}: {log.rpartition(chr(10))[2].strip()}", log)
    return log


def _docker(*args: str) -> str:
    result = subprocess.run(["docker", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise StepFailed(f"docker {' '.join(args[:2])}: {result.stderr.strip()}")
    return result.stdout
