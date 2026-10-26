"""Best melee gear for a budget - a multiple-choice knapsack.

Objective: maximise the total melee STRENGTH bonus (tie-break: best attack bonus of the set). Strength bonus is
what gear contributes to max hit:  Base Damage = 0.5 + Effective Strength x (Strength Bonus + 64) / 640
(https://oldschool.runescape.wiki/w/Maximum_melee_hit). Accuracy and defence are not optimised here - shown only.

Constraints:
- one item per slot; a two-handed weapon excludes weapon + shield;
- price = current instant-buy GE price (tradeable items only);
- every skill requirement <= the account's level AND <= the build's limit (a pure never gets a 40 Defence item);
- quest requirements must be in the completed list (otherwise the item is shown as "needs <quest>" and excluded);
- items whose requirement could not be read from the wiki are excluded unless `allow_unverified` (DECISIONS.md D3).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import duckdb

from gp.recommend.prices import Price
from gp.rules.builds import Build

MELEE_SLOTS = ["head", "cape", "neck", "body", "legs", "hands", "feet", "ring", "weapon", "shield", "2h"]
SOURCE = "https://oldschool.runescape.wiki/w/Maximum_melee_hit"


@dataclass(frozen=True)
class Candidate:
    name: str
    slot: str
    price: int
    strength: int
    attack: int  # best of stab/slash/crush
    verified: bool
    source_url: str


@dataclass
class GearSet:
    items: list[Candidate] = field(default_factory=list)
    excluded: dict[str, int] = field(default_factory=dict)  # reason -> count

    @property
    def price(self) -> int:
        return sum(c.price for c in self.items)

    @property
    def strength(self) -> int:
        return sum(c.strength for c in self.items)

    @property
    def attack(self) -> int:
        return sum(c.attack for c in self.items)


def candidates(
    con: duckdb.DuckDBPyConnection,
    prices_by_id: dict[int, Price],
    *,
    build: Build,
    levels: dict[str, int | None],
    done: set[str],
    members: bool,
    allow_unverified: bool = False,
) -> tuple[list[Candidate], dict[str, int]]:
    reqs: dict[str, list[tuple[str, str | None, int | None, str | None]]] = {}
    for page, kind, skill, level, quest in con.execute(
        "select page, kind, skill, level, quest from item_requirements"
    ).fetchall():
        reqs.setdefault(page, []).append((kind, skill, level, quest))
    rows = con.execute(
        """select item_id, name, page, slot, strength, stab_att, slash_att, crush_att, equip_req_status, members,
                  source_url
           from items where tradeable and slot in (select unnest(?))""",
        [MELEE_SLOTS],
    ).fetchall()
    excluded: dict[str, int] = {}
    best: dict[tuple[str, str], Candidate] = {}

    def skip(reason: str) -> None:
        excluded[reason] = excluded.get(reason, 0) + 1

    for item_id, name, page, slot, strength, stab, slash, crush, status, is_members, url in rows:
        if not members and is_members is not False:
            skip("members item (F2P)")
            continue
        price = prices_by_id.get(item_id)
        if price is None or price.buy is None:
            skip("no GE price")
            continue
        verified = status in ("found", "no_requirement")
        if not verified and not allow_unverified:
            skip("requirement unknown on the wiki")
            continue
        ok = True
        for kind, skill, level, quest in reqs.get(page, []):
            if kind == "skill" and (
                (levels.get(skill) or 1) < level or (skill in build.limits and level > build.limits[skill].max)
            ):
                skip(f"needs {skill} level")
                ok = False
                break
            if kind == "quest" and quest not in done:
                skip("needs a quest not marked done")
                ok = False
                break
        if not ok or ((strength or 0) <= 0 and max(stab or 0, slash or 0, crush or 0) <= 0):
            continue
        cand = Candidate(name, slot, price.buy, strength or 0, max(stab or 0, slash or 0, crush or 0), verified, url)
        key = (slot, name)
        if key not in best or cand.price < best[key].price:
            best[key] = cand
    return list(best.values()), excluded


def _pareto(options: list[Candidate]) -> list[Candidate]:
    """Drop items that are both more expensive and not better (keeps the DP small)."""
    options = sorted(options, key=lambda c: (c.price, -c.strength, -c.attack))
    front, best_score = [], (-1, -1)
    for c in options:
        if (c.strength, c.attack) > best_score:
            front.append(c)
            best_score = (c.strength, c.attack)
    return front


def best_set(cands: list[Candidate], budget: int, step: int | None = None) -> GearSet:
    """Multiple-choice knapsack over slot groups, prices bucketed to `step` gp (rounded up: never over budget)."""
    by_slot: dict[str, list[Candidate]] = {}
    for c in cands:
        by_slot.setdefault(c.slot, []).append(c)
    groups: list[list[tuple[Candidate, ...]]] = []
    for slot in ["head", "cape", "neck", "body", "legs", "hands", "feet", "ring"]:
        groups.append([(c,) for c in _pareto(by_slot.get(slot, []))])
    weapons = _pareto(by_slot.get("weapon", []))
    shields = _pareto(by_slot.get("shield", []))
    hand_options = [(c,) for c in _pareto(by_slot.get("2h", []))] + [(w,) for w in weapons] + [(s,) for s in shields]
    hand_options += [(w, s) for w in weapons for s in shields]
    groups.append(hand_options)

    step = step or max(1000, budget // 2000)
    cap = budget // step
    # dp[bucket] = (strength, attack, chosen tuples)
    dp: dict[int, tuple[int, int, tuple]] = {0: (0, 0, ())}
    for options in groups:
        nxt = dict(dp)
        for used, (st, at, chosen) in dp.items():
            for combo in options:
                cost = -(-sum(c.price for c in combo) // step)  # ceil
                total = used + cost
                if total > cap:
                    continue
                cand = (st + sum(c.strength for c in combo), at + sum(c.attack for c in combo), (*chosen, *combo))
                if total not in nxt or cand[:2] > nxt[total][:2]:
                    nxt[total] = cand
        dp = nxt
    _, _, chosen = max(dp.values(), key=lambda v: (v[0], v[1]))
    return GearSet(sorted(chosen, key=lambda c: MELEE_SLOTS.index(c.slot)))
