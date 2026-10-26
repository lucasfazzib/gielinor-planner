"""Builds are data (builds/*.yaml), not code. This module loads and validates them.

A build declares LIMITS (skills that must stay at or below a level). Everything numeric that the wiki already has -
the variants' target levels and combat ranges, the requirements of key unlock items - is joined from the store
at runtime instead of being copied into the YAML.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from gp.config import ROOT
from gp.normalize.quests import SKILLS
from gp.rules.xp import max_xp_staying_at

BUILDS_DIR = ROOT / "builds"


@dataclass(frozen=True)
class Limit:
    skill: str
    max: int
    kind: str  # hard: breaking it ruins the build; soft: a guideline (e.g. med Defence 70-80)
    why: str

    @property
    def max_xp(self) -> int:
        return max_xp_staying_at(self.max)


@dataclass(frozen=True)
class Build:
    id: str
    names: list[str]
    f2p_possible: bool
    source: str
    wiki_build: str
    summary: str
    limits: dict[str, Limit] = field(default_factory=dict)
    key_unlocks: list[str] = field(default_factory=list)


class BuildError(ValueError):
    pass


def _parse(path: Path) -> Build:
    data = yaml.safe_load(path.read_text())
    required = {"id", "names", "f2p_possible", "source", "wiki_build", "summary", "limits"}
    if missing := required - set(data):
        raise BuildError(f"{path.name}: missing {sorted(missing)}")
    if data["id"] != path.stem:
        raise BuildError(f"{path.name}: id {data['id']!r} must match the file name")
    limits = {}
    for skill, spec in (data["limits"] or {}).items():
        if skill not in SKILLS:
            raise BuildError(f"{path.name}: unknown skill {skill!r}")
        if spec.get("kind") not in {"hard", "soft"}:
            raise BuildError(f"{path.name}: {skill} limit needs kind: hard|soft")
        if not 1 <= int(spec["max"]) <= 99:
            raise BuildError(f"{path.name}: {skill} max must be 1..99")
        if skill == "Hitpoints" and int(spec["max"]) < 10:
            raise BuildError(f"{path.name}: Hitpoints starts at 10")
        limits[skill] = Limit(skill, int(spec["max"]), spec["kind"], spec.get("why", ""))
    return Build(
        id=data["id"],
        names=list(data["names"]),
        f2p_possible=bool(data["f2p_possible"]),
        source=data["source"],
        wiki_build=data["wiki_build"],
        summary=data["summary"],
        limits=limits,
        key_unlocks=[u["item"] for u in data.get("key_unlocks") or []],
    )


def load_builds(directory: Path = BUILDS_DIR) -> dict[str, Build]:
    return {b.id: b for b in (_parse(p) for p in sorted(directory.glob("*.yaml")))}


def find_build(name: str, directory: Path = BUILDS_DIR) -> Build:
    builds = load_builds(directory)
    key = name.strip().lower()
    for b in builds.values():
        if key == b.id or key in (n.lower() for n in b.names):
            return b
    raise BuildError(f"unknown build {name!r}; known: {', '.join(sorted(builds))}")


def violations(build: Build, levels: dict[str, int | None]) -> list[str]:
    """Limits the account already broke (unknown levels are not judged)."""
    return [
        f"{s} {levels[s]} > {lim.max} ({lim.kind})"
        for s, lim in build.limits.items()
        if levels.get(s) is not None and levels[s] > lim.max
    ]
