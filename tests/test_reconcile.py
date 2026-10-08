from datetime import datetime

from ctfdeploy.model import load_repo
from ctfdeploy.reconcile import _wanted
from ctfdeploy.schedule import desired_events


def test_reused_challenge_takes_current_version_and_stays_visible(repo):
    repo.challenge("sept/misc-reused", name="Old")
    repo.challenge("oct/misc-reused", name="New")
    loaded = load_repo(repo.root)
    now = datetime.fromisoformat("2026-09-20T00:00:00-05:00")

    wanted = _wanted(desired_events(loaded.events, now).events)

    assert [(c.name, visible) for c, visible in wanted] == [("New", True)]
