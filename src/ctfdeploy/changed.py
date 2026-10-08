from datetime import datetime
from pathlib import Path

from ctfdeploy.check import checked_events
from ctfdeploy.model import Challenge, Repo, git


def changed_hosted(repo: Repo, now: datetime, base: str) -> list[Challenge]:
    """Hosted challenges in the checked events whose files differ from `base`."""
    files = [Path(line) for line in git(repo.root, "diff", "--name-only", f"{base}...HEAD").split()]
    hosted = {c.dir: c for e in checked_events(repo, now) for c in e.challenges if c.compose}
    return [c for d, c in hosted.items() if any(f.is_relative_to(d) for f in files)]
