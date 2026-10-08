import json
import os
from pathlib import Path

import httpx


def notify_changes(failures: dict[str, str]) -> None:
    """Post to Discord when the set of failing things differs from the last run's."""
    state_dir, webhook = os.environ.get("STATE_DIRECTORY"), os.environ.get("DISCORD_WEBHOOK")
    if not state_dir or not webhook:
        return
    path = Path(state_dir) / "last.json"
    last = json.loads(path.read_text()) if path.exists() else {}
    lines = [f"❌ **{k}**: {v}" for k, v in failures.items() if k not in last]
    lines += [f"✅ **{k}** recovered" for k in last if k not in failures]
    if lines:
        try:
            httpx.post(webhook, json={"content": "\n".join(lines)[:2000]}, timeout=10)
        except httpx.HTTPError as e:
            print(f"discord: {e}")
    path.write_text(json.dumps(failures))
