from pathlib import Path
from textwrap import dedent

import pytest

CTFS = """\
host: ctf.isss.io
categories: [Web, Binary Exploitation]
events:
  - name: Sept
    start: 2026-09-18T18:00:00-05:00
    end: 2026-09-18T20:00:00-05:00
    challenges: sept
  - name: Oct
    start: 2026-10-02T18:00:00-05:00
    end: 2026-10-02T20:00:00-05:00
    challenges: oct
"""

CHALLENGE = """\
name: {name}
category: Web
description: Find the flag.
value: 100
flags:
  - utflag{{x}}
"""

COMPOSE = """\
services:
  web:
    build: .
    image: web:${{TAG:-dev}}
    ports:
      - {port}:80
"""


class RepoBuilder:
    def __init__(self, root: Path):
        self.root = root
        self.write("ctfs.yml", CTFS)
        (root / "sept").mkdir()
        (root / "oct").mkdir()

    def write(self, path: str, text: str) -> Path:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(dedent(text))
        return target

    def challenge(self, path: str, name: str | None = None, port: int | None = None, **text):
        self.write(f"{path}/challenge.yml", text.get("spec", CHALLENGE.format(name=name or path)))
        if port is not None or "compose" in text:
            self.write(f"{path}/docker-compose.yml", text.get("compose", COMPOSE.format(port=port)))


@pytest.fixture
def repo(tmp_path: Path) -> RepoBuilder:
    return RepoBuilder(tmp_path)
