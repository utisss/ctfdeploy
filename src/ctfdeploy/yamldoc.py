import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class Loader(yaml.SafeLoader):
    """SafeLoader with YAML 1.2 integers, as Docker reads compose files.

    YAML 1.1 reads an unquoted `7000:22` as the base-60 integer 420022.
    """


Loader.yaml_implicit_resolvers = {
    first: [(tag, regex) for tag, regex in resolvers if tag != "tag:yaml.org,2002:int"]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
Loader.add_implicit_resolver(
    "tag:yaml.org,2002:int", re.compile(r"^(?:[-+]?[0-9]+|0x[0-9a-fA-F]+)$"), list("-+0123456789")
)


@dataclass(frozen=True)
class YamlDoc:
    """A parsed YAML file that remembers which line each key is on."""

    path: Path
    data: Any
    node: yaml.Node | None

    @classmethod
    def load(cls, path: Path) -> "YamlDoc":
        text = path.read_text()
        return cls(path, yaml.load(text, Loader=Loader), yaml.compose(text, Loader=Loader))

    def line(self, *keys: str | int) -> int:
        """The 1-based line of the deepest key in `keys` that exists, or 1."""
        node, line = self.node, 1
        for key in keys:
            child = _child(node, key)
            if child is None:
                break
            line = child[0].start_mark.line + 1
            node = child[1]
        return line


def _child(node: yaml.Node | None, key: str | int) -> tuple[yaml.Node, yaml.Node] | None:
    if isinstance(node, yaml.MappingNode):
        for k, v in node.value:
            if k.value == key:
                return k, v
    if isinstance(node, yaml.SequenceNode) and isinstance(key, int) and key < len(node.value):
        item = node.value[key]
        return item, item
    return None
