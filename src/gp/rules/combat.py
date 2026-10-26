"""Combat level - formula from https://oldschool.runescape.wiki/w/Combat_level (rev 15305725):

    Base  = 1/4 * (Defence + Hitpoints + floor(Prayer / 2))
    Melee = 13/40 * (Attack + Strength)
    Range = 13/40 * floor(Ranged * 3/2)
    Mage  = 13/40 * floor(Magic * 3/2)
    Final = floor(Base + max(Melee, Range, Mage))

New players start at 3, the maximum is 126 (same page). Exact arithmetic with Fractions: floating point can put
a value like 79.99999 below an integer boundary and report the wrong level.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from math import floor

SOURCE = "https://oldschool.runescape.wiki/w/Combat_level"
COMBAT_SKILLS = ("Attack", "Strength", "Defence", "Hitpoints", "Ranged", "Magic", "Prayer")
DEFAULTS = {"Attack": 1, "Strength": 1, "Defence": 1, "Hitpoints": 10, "Ranged": 1, "Magic": 1, "Prayer": 1}


@dataclass(frozen=True)
class Combat:
    level: int
    exact: Fraction
    style: str  # which term won: melee / ranged / magic

    @property
    def progress(self) -> float:
        """How far into the current combat level (0..1) - useful to see how close the next level is."""
        return float(self.exact - self.level)


def combat(levels: dict[str, int | None]) -> Combat:
    lv = {s: (levels.get(s) if levels.get(s) is not None else DEFAULTS[s]) for s in COMBAT_SKILLS}
    base = Fraction(1, 4) * (lv["Defence"] + lv["Hitpoints"] + lv["Prayer"] // 2)
    terms = {
        "melee": Fraction(13, 40) * (lv["Attack"] + lv["Strength"]),
        "ranged": Fraction(13, 40) * (lv["Ranged"] * 3 // 2),
        "magic": Fraction(13, 40) * (lv["Magic"] * 3 // 2),
    }
    style = max(terms, key=lambda k: terms[k])
    exact = base + terms[style]
    return Combat(floor(exact), exact, style)


def levels_to_next_combat(levels: dict[str, int | None], cap: int = 99) -> dict[str, int | None]:
    """For each combat skill: how many levels of ONLY that skill raise the combat level by one (None = can't)."""
    current = combat(levels).level
    out: dict[str, int | None] = {}
    for skill in COMBAT_SKILLS:
        start = levels.get(skill) if levels.get(skill) is not None else DEFAULTS[skill]
        out[skill] = next(
            (n for n in range(1, cap - start + 1) if combat({**levels, skill: start + n}).level > current), None
        )
    return out
