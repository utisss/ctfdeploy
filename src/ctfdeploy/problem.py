from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Problem:
    path: Path
    line: int
    message: str
