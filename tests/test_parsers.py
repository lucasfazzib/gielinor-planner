"""Parsers against REAL responses recorded from the wiki and the hiscores.

Fixtures live in tests/fixtures and are refreshed with scripts/record_fixtures.py.
"""

import json
from pathlib import Path

import pytest

from gp.normalize.hiscores import levels, parse_hiscores
from gp.normalize.items import equip_requirements
from gp.normalize.quests import parse_requirements, parse_rewards

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def quest_rows():
    rows = json.loads((FIX / "bucket_quest.json").read_text())
    return {json.loads(r["json"])["name"] if r.get("json") else r["page_name"]: r for r in rows}


@pytest.fixture(scope="module")
def pages():
    return json.loads((FIX / "wikitext.json").read_text())


# ---------- quest requirements (bucket `quest`, rendered HTML) ----------
def test_fairytale_prerequisites_are_direct_quests_only(quest_rows):
    req = parse_requirements(quest_rows["Fairytale I - Growing Pains"]["requirements"])
    assert req.quests == ["Lost City", "Nature Spirit"]  # Priest in Peril / Restless Ghost are Nature Spirit's
    assert req.skills == []


def test_grand_tree_agility_is_boostable(quest_rows):
    req = parse_requirements(quest_rows["The Grand Tree"]["requirements"])
    agility = next(s for s in req.skills if s.skill == "Agility")
    assert agility.level == 25 and agility.boostable


# ---------- quest rewards ({{Quest rewards}} wikitext) ----------
def test_fairytale_fixed_xp(pages):
    rewards = parse_rewards(pages["Fairytale I - Growing Pains"]["content"])
    assert rewards.quest_points == 2
    fixed = {r.skills[0]: r.xp for r in rewards.xp if r.kind == "fixed"}
    assert fixed == {"Farming": 3500, "Attack": 2000, "Magic": 1000}


def test_observatory_xp_is_random_and_can_hit_defence(pages):
    rewards = parse_rewards(pages["Observatory Quest"]["content"]).xp
    random = [r for r in rewards if r.kind == "random"]
    assert len(random) == 1
    assert {"Attack", "Strength", "Defence", "Hitpoints"} <= set(random[0].skills)
    assert random[0].xp == 875
    assert [(r.kind, r.skills, r.xp) for r in rewards if r.kind == "fixed"] == [("fixed", ["Crafting"], 2250)]


def test_single_skill_choice_is_safe_choice(pages):
    """Fremennik Isles: two lots of 10,000 in ONE of Strength/Attack/Defence/Hitpoints -> a real choice."""
    (choice,) = [r for r in parse_rewards(pages["The Fremennik Isles"]["content"]).xp if r.kind == "choice"]
    assert set(choice.skills) == {"Attack", "Strength", "Defence", "Hitpoints"} and choice.xp == 10000


def test_monkey_madness_package_choice_is_not_safe(pages):
    """Monkey Madness I: 35k Atk+Def & 20k Str+HP OR the reverse -> Defence XP either way (worst case)."""
    rewards = parse_rewards(pages["Monkey Madness I"]["content"]).xp
    (bundle,) = [r for r in rewards if r.kind == "choice_bundle"]
    assert "Defence" in bundle.skills and bundle.xp == 35000
    assert not [r for r in rewards if r.kind == "choice"]


def test_waterfall_gives_attack_and_strength(pages):
    fixed = {r.skills[0]: r.xp for r in parse_rewards(pages["Waterfall Quest"]["content"]).xp if r.kind == "fixed"}
    assert fixed.get("Attack", 0) > 0 and fixed.get("Strength", 0) > 0
    assert "Defence" not in fixed


def test_any_combat_skill_except_prayer_is_a_choice():
    wikitext = (
        "{{Quest rewards\n|qp = 1\n|rewards = *10,000 [[experience]] in any [[combat]] skill other than [[Prayer]].\n}}"
    )
    (reward,) = parse_rewards(wikitext).xp
    assert reward.kind == "choice" and reward.xp == 10000


def test_generic_quest_link_is_not_a_quest_requirement():
    text = (
        "The '''x''' requires 40 [[Defence]] to wear and players must have completed the [[quest]] [[Dragon Slayer I]]."
    )
    assert equip_requirements(text).quests == ["Dragon Slayer I"]


def test_page_without_template():
    assert parse_rewards("just text").found_template is False


# ---------- equip requirements (lead sentence) ----------
def test_rune_scimitar_needs_40_attack(pages):
    reqs = equip_requirements(pages["Rune scimitar"]["content"])
    assert reqs.skills == [("Attack", 40)]
    assert "40 Attack" in reqs.evidence


def test_dragon_scimitar_needs_60_attack_and_monkey_madness(pages):
    reqs = equip_requirements(pages["Dragon scimitar"]["content"])
    assert reqs.skills == [("Attack", 60)]
    assert reqs.quests == ["Monkey Madness I"]


def test_berserker_helm_needs_45_defence_and_fremennik_trials(pages):
    reqs = equip_requirements(pages["Berserker helm"]["content"])
    assert reqs.skills == [("Defence", 45)]
    assert reqs.quests == ["The Fremennik Trials"]


def test_barrows_gloves_negated_requirement_is_not_a_requirement(pages):
    """'While these gloves lack a Defence requirement ...' must not produce a Defence requirement."""
    assert equip_requirements(pages["Barrows gloves"]["content"]).skills == []


# ---------- hiscores ----------
def test_hiscores_json_has_named_skills_including_sailing():
    stats = parse_hiscores(json.loads((FIX / "hiscores_lynx_titan.json").read_text()))
    names = [s.skill for s in stats]
    assert names[0] == "Overall" and "Sailing" in names and len(names) == 25
    assert levels(stats)["Attack"] == 99


def test_unranked_is_unknown_not_one():
    stats = parse_hiscores(
        {
            "skills": [
                {"name": "Overall", "level": 500, "xp": 1, "rank": 1},
                {"name": "Defence", "level": -1, "xp": -1, "rank": -1},
            ]
        }
    )
    assert levels(stats) == {"Defence": None}
    assert levels(stats, {"Defence": 1}) == {"Defence": 1}
