import hashlib
from pathlib import Path

from jinja2 import Template

from ctfdeploy.ctfd import Ctfd, CtfdError
from ctfdeploy.model import Challenge, Event, Repo

DYNAMIC = ("initial", "decay", "minimum", "function")


def sync_challenge(
    ctfd: Ctfd, challenge: Challenge, visible: bool, cid: int | None
) -> tuple[int, str, int | None]:
    """Create or update one challenge.

    Returns its id, what changed, e.g. "state, flags", and its current `next_id`.
    """
    body = _fields(challenge, visible)
    if cid is None:
        current = ctfd.post("/challenges", body)
        cid = current["id"]
        changed = ["created"]
    else:
        current = ctfd.get(f"/challenges/{cid}?view=admin")
        if current["type"] != body["type"]:
            raise CtfdError(f"type changed to {body['type']}: delete the challenge in CTFd first")
        changed = sorted(k for k, v in body.items() if current.get(k) != v)
        if changed:
            ctfd.patch(f"/challenges/{cid}", {k: body[k] for k in changed})
    for sync in (_sync_flags, _sync_hints, _sync_tags, _sync_files):
        changed += sync(ctfd, cid, challenge)
    return cid, ", ".join(changed) or "unchanged", current.get("next_id")


def link_next(
    ctfd: Ctfd, challenges: list[Challenge], ids: dict[str, int], next_ids: dict[int, int | None]
) -> str:
    """Point each challenge's `next` at its target, once every challenge exists.

    `next_ids` holds the `next_id` already read for a challenge id; the rest are fetched.
    """
    changed = []
    for challenge in challenges:
        cid, target = ids.get(challenge.name), ids.get(challenge.spec.get("next"))
        if not cid:
            continue
        if cid not in next_ids:
            next_ids[cid] = ctfd.get(f"/challenges/{cid}?view=admin")["next_id"]
        if next_ids[cid] != target:
            ctfd.patch(f"/challenges/{cid}", {"next_id": target})
            changed.append(challenge.slug)
    return ", ".join(changed) or "unchanged"


def sync_config(ctfd: Ctfd, repo: Repo, event: Event) -> str:
    """CTFd's name, start, freeze and homepage follow `event`. The board never closes."""
    wanted = {
        "ctf_name": event.name,
        "start": str(int(event.start.timestamp())),
        "freeze": str(int(event.end.timestamp())) if event.end else None,
        "end": None,
    }
    current = {c["key"]: c["value"] for c in ctfd.get("/configs")}
    changed = sorted(k for k, v in wanted.items() if current.get(k) != v)
    if changed:
        ctfd.patch("/configs", {k: wanted[k] for k in changed})
    if repo.homepage_template:
        changed += _sync_homepage(ctfd, Template(repo.homepage_template).render(event=event))
    return ", ".join(changed) or "unchanged"


def _sync_homepage(ctfd: Ctfd, content: str) -> list[str]:
    page = next((p for p in ctfd.get("/pages") if p["route"] == "index"), None)
    if page is None:
        page = {"title": "Home", "route": "index", "format": "html", "content": content}
        ctfd.post("/pages", page)
    elif ctfd.get(f"/pages/{page['id']}")["content"] != content:
        ctfd.patch(f"/pages/{page['id']}", {"content": content})
    else:
        return []
    return ["homepage"]


def _fields(challenge: Challenge, visible: bool) -> dict:
    spec = challenge.spec
    body = {
        "name": challenge.name,
        "category": spec["category"],
        "description": spec["description"],
        "connection_info": spec.get("connection_info"),
        "state": "visible" if visible else "hidden",
        "type": spec.get("type", "standard"),
    }
    if body["type"] == "dynamic":
        extra = spec["extra"]
        body |= {k: extra[k] for k in DYNAMIC if k in extra}
        body.setdefault("function", "logarithmic")
    else:
        body["value"] = spec["value"]
    return body


def _sync_flags(ctfd: Ctfd, cid: int, challenge: Challenge) -> list[str]:
    flags = challenge.spec["flags"]
    wanted = [
        {"type": "static", "data": None} | (f if isinstance(f, dict) else {"content": str(f)})
        for f in flags
    ]
    return _sync_set(ctfd, cid, "flags", wanted, ("type", "content", "data"))


def _sync_hints(ctfd: Ctfd, cid: int, challenge: Challenge) -> list[str]:
    hints = challenge.spec.get("hints") or []
    wanted = [{"cost": 0} | (h if isinstance(h, dict) else {"content": str(h)}) for h in hints]
    return _sync_set(ctfd, cid, "hints", wanted, ("content", "cost"))


def _sync_tags(ctfd: Ctfd, cid: int, challenge: Challenge) -> list[str]:
    wanted = [{"value": str(t)} for t in challenge.spec.get("tags") or []]
    return _sync_set(ctfd, cid, "tags", wanted, ("value",))


def _sync_set(ctfd: Ctfd, cid: int, kind: str, wanted: list[dict], fields: tuple) -> list[str]:
    """Make a challenge's flags, hints or tags equal `wanted`, compared on `fields`."""

    def key(item: dict) -> tuple:
        return tuple(item.get(f) or None for f in fields)

    wanted_by_key = {key(item): item for item in wanted}
    current = {key(item): item["id"] for item in ctfd.get(f"/challenges/{cid}/{kind}")}
    for stale in current.keys() - wanted_by_key.keys():
        ctfd.delete(f"/{kind}/{current[stale]}")
    for missing in wanted_by_key.keys() - current.keys():
        ctfd.post(f"/{kind}", {"challenge": cid, **wanted_by_key[missing]})
    return [kind] if current.keys() != wanted_by_key.keys() else []


def _sync_files(ctfd: Ctfd, cid: int, challenge: Challenge) -> list[str]:
    paths = [challenge.path / name for name in challenge.spec.get("files") or []]
    wanted = {(p.name, hashlib.sha1(p.read_bytes()).hexdigest()): p for p in paths}
    current = {
        (Path(f["location"]).name, f["sha1sum"]): f["id"]
        for f in ctfd.get(f"/challenges/{cid}/files")
    }
    for stale in current.keys() - wanted.keys():
        ctfd.delete(f"/files/{current[stale]}")
    for missing in wanted.keys() - current.keys():
        with wanted[missing].open("rb") as fh:
            ctfd.post(
                "/files",
                files={"file": (missing[0], fh)},
                data={"challenge": cid, "type": "challenge"},
            )
    return ["files"] if current.keys() != wanted.keys() else []
