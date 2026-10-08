import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

from ctfdeploy.yamldoc import YamlDoc

META_FILE = "ctfs.yml"
CHALLENGE_FILE = "challenge.yml"
COMPOSE_FILES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")


class RepoError(Exception):
    """`ctfs.yml` is unusable, so nothing else can be trusted."""

    def __init__(self, path: Path, line: int, message: str):
        super().__init__(message)
        self.path, self.line = path, line


@dataclass(frozen=True)
class Challenge:
    root: Path
    dir: Path
    doc: YamlDoc
    compose: Path | None

    @property
    def slug(self) -> str:
        return self.dir.name

    @property
    def stack(self) -> str:
        return f"chall-{self.slug.lower()}"

    @property
    def name(self) -> str:
        return str(self.doc.data.get("name", self.slug))

    @property
    def spec(self) -> dict:
        return self.doc.data

    @property
    def path(self) -> Path:
        return self.root / self.dir

    def version(self) -> str:
        """The git tree hash of the challenge directory, so only content changes roll out."""
        result = subprocess.run(
            ["git", "rev-parse", f"HEAD:{self.dir.as_posix()}"],
            cwd=self.root,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()[:12] if result.returncode == 0 else "dev"


@dataclass(frozen=True)
class Event:
    name: str
    start: datetime
    end: datetime | None
    dir: Path
    challenges: tuple[Challenge, ...] = field(default=(), compare=False)


@dataclass(frozen=True)
class Repo:
    root: Path
    doc: YamlDoc
    host: str
    homepage_template: str
    categories: tuple[str, ...]
    events: tuple[Event, ...]

    @property
    def meta(self) -> Path:
        return self.root / META_FILE


def load_repo(root: Path) -> Repo:
    path = root / META_FILE
    if not path.exists():
        raise RepoError(path, 1, f"{META_FILE} not found")
    doc = _load(path)
    host = _required(doc, "host", str)
    events = tuple(
        sorted((_event(root, doc, i) for i in range(len(_events(doc)))), key=lambda e: e.start)
    )
    return Repo(
        root=root,
        doc=doc,
        host=host,
        homepage_template=doc.data.get("homepage_template", ""),
        categories=tuple(doc.data.get("categories", ())),
        events=events,
    )


def _events(doc: YamlDoc) -> list:
    events = doc.data.get("events")
    if not isinstance(events, list) or not events:
        raise RepoError(doc.path, doc.line("events"), "events must be a non-empty list")
    return events


def _event(root: Path, doc: YamlDoc, i: int) -> Event:
    raw = doc.data["events"][i]
    if not isinstance(raw, dict):
        raise RepoError(doc.path, doc.line("events", i), "each event must be a mapping")
    for key in ("name", "start", "challenges"):
        if key not in raw:
            raise RepoError(doc.path, doc.line("events", i), f"event is missing {key}")
    start = _aware(doc, raw["start"], ("events", i, "start"))
    end = _aware(doc, raw["end"], ("events", i, "end")) if raw.get("end") else None
    if end and end <= start:
        raise RepoError(doc.path, doc.line("events", i, "end"), "end must be after start")
    event_dir = Path(raw["challenges"])
    if not (root / event_dir).is_dir():
        raise RepoError(
            doc.path, doc.line("events", i, "challenges"), f"{event_dir} is not a directory"
        )
    return Event(str(raw["name"]), start, end, event_dir, _challenges(root, event_dir))


def _aware(doc: YamlDoc, value, keys: tuple) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise RepoError(
            doc.path,
            doc.line(*keys),
            "times must be ISO 8601 with a UTC offset, e.g. 2026-10-02T18:05:00-05:00",
        )
    return value


def _required(doc: YamlDoc, key: str, kind: type):
    value = doc.data.get(key)
    if not isinstance(value, kind):
        raise RepoError(doc.path, doc.line(key), f"{key} is required")
    return value


def _challenges(root: Path, event_dir: Path) -> tuple[Challenge, ...]:
    found = []
    for path in sorted((root / event_dir).iterdir()):
        if not (path / CHALLENGE_FILE).is_file():
            continue
        compose = next((path / f for f in COMPOSE_FILES if (path / f).is_file()), None)
        found.append(
            Challenge(
                root=root,
                dir=path.relative_to(root),
                doc=_load(path / CHALLENGE_FILE),
                compose=compose,
            )
        )
    return tuple(found)


def _load(path: Path) -> YamlDoc:
    try:
        doc = YamlDoc.load(path)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        raise RepoError(path, mark.line + 1 if mark else 1, f"invalid YAML: {e}") from e
    if not isinstance(doc.data, dict):
        raise RepoError(path, 1, f"{path.name} must be a mapping")
    return doc
