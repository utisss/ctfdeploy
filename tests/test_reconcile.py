from datetime import datetime

import pytest
from conftest import CHALLENGE

from ctfdeploy import docker
from ctfdeploy import reconcile as reconcile_module
from ctfdeploy.model import load_repo
from ctfdeploy.reconcile import _deploy, _wanted, reconcile
from ctfdeploy.report import Report
from ctfdeploy.schedule import desired_events


def test_reused_challenge_takes_current_version_and_stays_visible(repo):
    repo.challenge("sept/misc-reused", name="Old")
    repo.challenge("oct/misc-reused", name="New")
    loaded = load_repo(repo.root)
    now = datetime.fromisoformat("2026-09-20T00:00:00-05:00")

    wanted = _wanted(desired_events(loaded.events, now).events)

    assert [(c.name, visible) for c, visible in wanted] == [("New", True)]


class FakeCtfd:
    def __init__(self):
        self.challenges: dict[int, dict] = {}
        self.calls: list[str] = []

    def get(self, path: str):
        self.calls.append(f"GET {path}")
        if path == "/challenges?view=admin":
            return list(self.challenges.values())
        if path.endswith("?view=admin"):
            return self.challenges[int(path.split("/")[2].split("?")[0])]
        return []

    def post(self, path: str, json=None, **kwargs):
        self.calls.append(f"POST {path}")
        if path == "/challenges":
            cid = len(self.challenges) + 1
            self.challenges[cid] = {"id": cid, "next_id": None, **json}
            return self.challenges[cid]
        return {}

    def patch(self, path: str, json: dict):
        self.calls.append(f"PATCH {path}")
        if path.startswith("/challenges/"):
            self.challenges[int(path.split("/")[2])].update(json)

    def delete(self, path: str) -> None:
        self.calls.append(f"DELETE {path}")


def test_reconcile_links_next_without_refetching(repo, monkeypatch):
    for slug, nxt in [("web-a", "web-b"), ("web-b", None), ("web-c", None)]:
        spec = CHALLENGE.format(name=slug) + (f"next: {nxt}\n" if nxt else "")
        repo.challenge(f"oct/{slug}", spec=spec)
    monkeypatch.setattr(docker, "deployed_tags", dict)
    monkeypatch.setattr(reconcile_module, "datetime", FixedNow)
    ctfd = FakeCtfd()

    assert reconcile(repo.root, ctfd, Report(repo.root, github=False), fetch=False) == 0
    assert [c["next_id"] for c in ctfd.challenges.values()] == [2, None, None]

    ctfd.calls.clear()
    assert reconcile(repo.root, ctfd, Report(repo.root, github=False), fetch=False) == 0
    assert sum(call.endswith("?view=admin") for call in ctfd.calls) == 1 + 3
    assert not [call for call in ctfd.calls if call.startswith("PATCH /challenges")]


class FixedNow(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime.fromisoformat("2026-10-02T19:00:00-05:00")


def test_stack_with_a_stock_image_is_up_to_date(repo, monkeypatch):
    repo.challenge("oct/web-db", port=7001)
    challenge = load_repo(repo.root).events[1].challenges[0]
    monkeypatch.setattr(challenge.__class__, "version", lambda self: "abc123")
    monkeypatch.setattr(docker, "build", lambda *a: pytest.fail("rebuilt"))

    assert _deploy(challenge, {"abc123", "10.11"}, []) == "up to date abc123"
