import os
from pathlib import Path

from ctfdeploy.problem import Problem


class Report:
    """Prints for a terminal, or with GitHub Actions workflow commands."""

    def __init__(self, root: Path, github: bool | None = None):
        self.root = root
        self.github = os.environ.get("GITHUB_ACTIONS") == "true" if github is None else github

    def problem(self, problem: Problem, title: str = "") -> None:
        path = _relative(problem.path, self.root)
        if self.github:
            title = f",title={_escape(title, prop=True)}" if title else ""
            print(f"::error file={path},line={problem.line}{title}::{_escape(problem.message)}")
        else:
            prefix = f"{title}: " if title else ""
            print(f"{path}:{problem.line}: {prefix}{problem.message}")

    def error(self, message: str, title: str = "") -> None:
        if self.github:
            title = f" title={_escape(title, prop=True)}" if title else ""
            print(f"::error{title}::{_escape(message)}")
        else:
            print(f"error: {title + ': ' if title else ''}{message}")

    def group(self, title: str, body: str) -> None:
        """A finished step: collapsed under its title on GitHub, indented in a terminal."""
        if self.github:
            print(f"::group::{title}\n{body.rstrip()}\n::endgroup::", flush=True)
        else:
            print(title)
            if body.strip():
                print("".join(f"    {line}\n" for line in body.rstrip().splitlines()), end="")


def _relative(path: Path, root: Path) -> str:
    return str(path.relative_to(root)) if path.is_relative_to(root) else str(path)


def _escape(text: str, prop: bool = False) -> str:
    text = text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return text.replace(":", "%3A").replace(",", "%2C") if prop else text
