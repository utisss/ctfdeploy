import hashlib
from pathlib import Path

from jinja2 import Template

from ctfdeploy.ctfd import Ctfd, CtfdError
from ctfdeploy.model import Challenge, Event, Repo

DYNAMIC = ("initial", "decay", "minimum", "function")
FLAG = ("type", "content", "data")


def sync_challenge(
    ctfd: Ctfd, challenge: Challenge, visible: bool, cid: int | None
) -> tuple[int, str]:
    """Create or update one challenge. Returns its id and what changed, e.g. "state, flags"."""
    body = _fields(challenge, visible)
    if cid is None:
        cid = ctfd.post("/challenges", body)["id"]
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
    return cid, ", ".join(changed) or "unchanged"


def link_next(ctfd: Ctfd, challenges: list[Challenge], ids: dict[str, int]) -> str:
    """Point each challenge's `next` at its target, once every challenge exists."""
    changed = []
    for challenge in challenges:
        cid, target = ids.get(challenge.name), ids.get(challenge.spec.get("next"))
        if cid and ctfd.get(f"/challenges/{cid}?view=admin")["next_id"] != target:
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
    def key(flag) -> tuple:
        if isinstance(flag, dict):
            return flag.get("type", "static"), flag["content"], flag.get("data") or None
        return "static", str(flag), None

    wanted = {key(f) for f in challenge.spec["flags"]}
    return _sync_set(ctfd, cid, "flags", wanted, key, lambda k: dict(zip(FLAG, k, strict=True)))


def _sync_hints(ctfd: Ctfd, cid: int, challenge: Challenge) -> list[str]:
    def key(hint) -> tuple:
        if isinstance(hint, dict):
            return hint["content"], hint.get("cost", 0)
        return str(hint), 0

    wanted = {key(h) for h in challenge.spec.get("hints") or []}
    return _sync_set(ctfd, cid, "hints", wanted, key, lambda k: {"content": k[0], "cost": k[1]})


def _sync_tags(ctfd: Ctfd, cid: int, challenge: Challenge) -> list[str]:
    wanted = {str(t) for t in challenge.spec.get("tags") or []}
    return _sync_set(
        ctfd, cid, "tags", wanted, lambda t: t if isinstance(t, str) else t["value"],
        lambda k: {"value": k},
    )  # fmt: skip


def _sync_set(ctfd: Ctfd, cid: int, kind: str, wanted: set, key, to_body) -> list[str]:
    current = {key(item): item["id"] for item in ctfd.get(f"/challenges/{cid}/{kind}")}
    for stale in current.keys() - wanted:
        ctfd.delete(f"/{kind}/{current[stale]}")
    for missing in wanted - current.keys():
        ctfd.post(f"/{kind}", {"challenge": cid, **to_body(missing)})
    return [kind] if current.keys() != wanted else []


def _sync_files(ctfd: Ctfd, cid: int, challenge: Challenge) -> list[str]:
    wanted = {Path(name).name: challenge.path / name for name in challenge.spec.get("files") or []}
    digests = {name: hashlib.sha1(path.read_bytes()).hexdigest() for name, path in wanted.items()}
    current = {Path(f["location"]).name: f for f in ctfd.get(f"/challenges/{cid}/files")}
    stale = [f for name, f in current.items() if digests.get(name) != f["sha1sum"]]
    missing = [name for name in wanted if name not in current or current[name] in stale]
    for f in stale:
        ctfd.delete(f"/files/{f['id']}")
    for name in missing:
        with wanted[name].open("rb") as fh:
            ctfd.post(
                "/files", files={"file": (name, fh)}, data={"challenge": cid, "type": "challenge"}
            )
    return ["files"] if stale or missing else []
