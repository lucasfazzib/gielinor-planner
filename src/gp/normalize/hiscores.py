"""Official hiscores (index_lite.json): {"name", "skills": [{id, name, rank, level, xp}], "activities": [...]}.

Unranked values come back as -1 (the player is below the board's ranking threshold for that skill).
The real level is then UNKNOWN - not necessarily 1 - so we keep NULL and let the user override it
(`gp stats --set Defence=1`) instead of guessing.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SkillStat:
    skill: str
    level: int | None
    xp: int | None
    rank: int | None


def parse_hiscores(payload: dict) -> list[SkillStat]:
    out = []
    for s in payload.get("skills", []):
        out.append(
            SkillStat(
                skill=s["name"],
                level=s["level"] if s.get("level", -1) >= 0 else None,
                xp=s["xp"] if s.get("xp", -1) >= 0 else None,
                rank=s["rank"] if s.get("rank", -1) >= 0 else None,
            )
        )
    return out


def levels(stats: list[SkillStat], overrides: dict[str, int] | None = None) -> dict[str, int | None]:
    """Skill -> level (None = unranked and not overridden)."""
    out = {s.skill: s.level for s in stats if s.skill != "Overall"}
    out.update(overrides or {})
    return out
