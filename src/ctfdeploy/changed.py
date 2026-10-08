import subprocess
from datetime import datetime
from pathlib import Path

from ctfdeploy.check import checked_events
from ctfdeploy.model import Challenge, Repo, RepoError


def changed_hosted(repo: Repo, now: datetime, base: str) -> list[Challenge]:
    """Hosted challenges in the checked events whose files differ from `base`."""
    cmd = ["git", "diff", "--name-only", f"{base}...HEAD"]
    diff = subprocess.run(cmd, cwd=repo.root, capture_output=True, text=True)
    if diff.returncode != 0:
        raise RepoError(repo.root, 1, f"{' '.join(cmd)}: {diff.stderr.strip()}")
    files = [Path(line) for line in diff.stdout.splitlines()]
    hosted = {c.dir: c for e in checked_events(repo, now) for c in e.challenges if c.compose}
    return [c for d, c in hosted.items() if any(f.is_relative_to(d) for f in files)]
