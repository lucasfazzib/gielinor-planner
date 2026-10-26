"""Experience table - formula from https://oldschool.runescape.wiki/w/Experience (rev 15374571):

    Experience(L) = floor( 1/4 * sum_{l=1}^{L-1} floor( l + 300 * 2^(l/7) ) )

Level 99 = 13,034,431 (the wiki's table; asserted in tests). Virtual levels up to 126 follow the same formula.
"""

from __future__ import annotations

from bisect import bisect_right
from functools import cache

SOURCE = "https://oldschool.runescape.wiki/w/Experience"
MAX_LEVEL = 99
MAX_XP = 200_000_000


@cache
def xp_table(max_level: int = 126) -> tuple[int, ...]:
    """xp_table()[L] = experience needed for level L (index 0 unused)."""
    table, points = [0, 0], 0.0
    for level in range(1, max_level):
        points += int(level + 300 * 2 ** (level / 7))
        table.append(int(points / 4))
    return tuple(table)


def xp_for_level(level: int) -> int:
    return xp_table()[level]


def level_for_xp(xp: int, cap: int = MAX_LEVEL) -> int:
    return min(cap, bisect_right(xp_table(), xp) - 1)


def max_xp_staying_at(level: int) -> int:
    """Highest experience that still keeps the skill at `level` (one point below the next level)."""
    return MAX_XP if level >= MAX_LEVEL else xp_for_level(level + 1) - 1
