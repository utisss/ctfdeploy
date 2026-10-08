import subprocess
import sys

from ctfdeploy.check import connection_port
from ctfdeploy.compose import published_ports
from ctfdeploy.docker import StepFailed
from ctfdeploy.model import Challenge
from ctfdeploy.yamldoc import YamlDoc

SOLVE_FILE = "solve.py"
TIMEOUT = 120


def has_solve(challenge: Challenge) -> bool:
    return (challenge.path / SOLVE_FILE).is_file()


def solve(challenge: Challenge, host: str = "127.0.0.1") -> str:
    """Run `solve.py HOST PORT` with uv, which installs its inline (PEP 723) dependencies.

    Requires a flag in its output.
    """
    port = _port(challenge)
    try:
        result = subprocess.run(
            ["uv", "run", "--python", sys.executable, "--script", SOLVE_FILE, host, str(port)],
            cwd=challenge.path,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=TIMEOUT,
        )
    except subprocess.TimeoutExpired as e:
        raise StepFailed(f"solve.py ran past {TIMEOUT}s", str(e.output or "")) from e
    flags = [f["content"] if isinstance(f, dict) else str(f) for f in challenge.spec["flags"]]
    if not any(flag in result.stdout for flag in flags):
        reason = f"exit code {result.returncode}" if result.returncode else "no flag in its output"
        raise StepFailed(f"solve.py: {reason}", result.stdout)
    return result.stdout


def _port(challenge: Challenge) -> int:
    port = connection_port(challenge.spec.get("connection_info"))
    if port is None and challenge.compose:
        ports = published_ports(YamlDoc.load(challenge.path / challenge.compose))
        port = ports[0][0] if ports else None
    if port is None:
        raise StepFailed("no port to solve against: set connection_info or publish a port")
    return port
