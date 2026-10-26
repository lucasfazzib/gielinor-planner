"""Completed quests. The hiscores do not expose quest completion, so the player keeps a local list
(data/quests_done.txt, git-ignored, one quest name per line) through `gp done`."""

from __future__ import annotations

from pathlib import Path

from gp.config import settings


def done_path() -> Path:
    return settings().data_dir / "quests_done.txt"


def load_done(path: Path | None = None) -> set[str]:
    path = path or done_path()
    if not path.exists():
        return set()
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def save_done(names: set[str], path: Path | None = None) -> None:
    path = path or done_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(sorted(names)) + "\n", encoding="utf-8")


def resolve(name: str, known: set[str]) -> str | None:
    """Case-insensitive exact match against the quest names in the store."""
    by_lower = {k.lower(): k for k in known}
    return by_lower.get(name.strip().lower())
