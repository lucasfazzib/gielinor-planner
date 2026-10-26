"""Training recipes (Bucket `recipe`) and money-making methods (Bucket `money_making_guide`).

Recipe `production_json` (verified 2026-10-26):
    {"ticks": "3", "materials": [{"name", "quantity"}], "skills": [{"name", "level", "experience", "boostable"}],
     "members": true, "output": {"name", "quantity"}, "tools": "...", "facilities": "..."}
Money making guide `json`:
    {"activity", "category", "members", "skill": <rendered scp spans>, "prices": {"default_kph", "kph_text", ...},
     "inputs": [{"name", "qty", "isph"}], "outputs": [{"name", "qty", "isph"}]}
`isph` = quantity is per hour (true) or per action (false, multiply by actions per hour).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from gp.normalize.quests import parse_requirements


def _num(value) -> float | None:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


@dataclass
class Recipe:
    name: str
    skill: str
    level: int
    xp: float
    ticks: float | None
    members: bool | None
    materials: list[tuple[str, float]]
    output: tuple[str, float] | None
    other_skills: list[tuple[str, int]] = field(default_factory=list)


def parse_recipe(page_name_sub: str, production_json: str) -> list[Recipe]:
    """One row per skill that gives XP (a recipe can train two skills at once)."""
    data = json.loads(production_json or "{}")
    skills = [(s.get("name"), _num(s.get("level")), _num(s.get("experience"))) for s in data.get("skills", [])]
    materials = [(m["name"], _num(m.get("quantity")) or 1.0) for m in data.get("materials", []) if m.get("name")]
    out = data.get("output") or {}
    output = (out["name"], _num(out.get("quantity")) or 1.0) if out.get("name") else None
    recipes = []
    for name, level, xp in skills:
        if not name or level is None or not xp:
            continue
        others = [(n, int(lv)) for n, lv, _ in skills if n != name and lv is not None]
        recipes.append(
            Recipe(
                page_name_sub,
                name,
                int(level),
                xp,
                _num(data.get("ticks")),
                data.get("members"),
                materials,
                output,
                others,
            )
        )
    return recipes


@dataclass
class MoneyMethod:
    page: str
    activity: str
    category: str | None
    members: bool | None
    recurring: bool | None
    actions_per_hour: float | None
    actions_label: str | None
    skill_reqs: list[tuple[str, int]]
    inputs: list[tuple[str, float, bool]]  # (item, qty, per_hour)
    outputs: list[tuple[str, float, bool]]
    wiki_value_per_hour: float | None


def parse_money_method(page: str, raw_json: str, recurring: bool | None, value) -> MoneyMethod:
    data = json.loads(raw_json or "{}")
    prices = data.get("prices") or {}
    reqs = parse_requirements(data.get("skill") or "")

    def items(key):
        return [(i["name"], _num(i.get("qty")) or 0.0, bool(i.get("isph"))) for i in data.get(key, []) if i.get("name")]

    return MoneyMethod(
        page=page,
        activity=data.get("activity") or page.split("/", 1)[-1],
        category=data.get("category"),
        members=data.get("members"),
        recurring=recurring,
        actions_per_hour=_num(prices.get("default_kph")),
        actions_label=prices.get("kph_text"),
        skill_reqs=[(s.skill, s.level) for s in reqs.skills],
        inputs=items("inputs"),
        outputs=items("outputs"),
        wiki_value_per_hour=_num(value),
    )
