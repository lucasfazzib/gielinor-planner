"""Rules: XP table, combat formula, build YAMLs and the XP guard.

Expected numbers come from the wiki (Experience / Combat level pages, build variants in Bucket `infobox_pure`)
and from real quest rewards stored by phase 1 (Dragon Slayer I, The Fremennik Isles, Monkey Madness I).
"""

from pathlib import Path

import pytest

from gp.rules.builds import BuildError, find_build, load_builds, violations
from gp.rules.combat import combat, levels_to_next_combat
from gp.rules.guard import QuestFacts, judge_all, judge_one
from gp.rules.xp import level_for_xp, max_xp_staying_at, xp_for_level

FRESH_XP = {"Attack": 0, "Strength": 0, "Defence": 0, "Hitpoints": 1154, "Ranged": 0, "Magic": 0, "Prayer": 0}


# ---------- experience table (wiki "Experience") ----------
def test_xp_table_matches_the_wiki():
    assert xp_for_level(2) == 83
    assert xp_for_level(10) == 1154
    assert xp_for_level(99) == 13_034_431


def test_level_for_xp_and_headroom():
    assert level_for_xp(82) == 1 and level_for_xp(83) == 2
    assert max_xp_staying_at(1) == 82  # a 1-Defence pure can absorb at most 82 Defence XP


# ---------- combat formula (wiki "Combat level") ----------
def test_fresh_account_is_3_and_maxed_is_126():
    assert combat({}).level == 3
    assert (
        combat({s: 99 for s in ("Attack", "Strength", "Defence", "Hitpoints", "Ranged", "Magic", "Prayer")}).level
        == 126
    )


@pytest.mark.parametrize(
    ("levels", "expected"),
    [
        # One-defence pure variant (wiki: combat 79)
        ({"Attack": 50, "Strength": 99, "Defence": 1, "Prayer": 52, "Ranged": 99, "Magic": 99, "Hitpoints": 99}, 79),
        # Berserker variant (wiki: combat 83)
        ({"Attack": 50, "Strength": 99, "Defence": 45, "Prayer": 31, "Ranged": 90, "Magic": 94, "Hitpoints": 80}, 83),
        # Obsidian mauler variant: the wiki infobox says 55, the formula gives 57 (reported by `gp quality`)
        ({"Attack": 1, "Strength": 99, "Defence": 1, "Prayer": 1, "Ranged": 1, "Magic": 50, "Hitpoints": 99}, 57),
    ],
)
def test_wiki_build_variants(levels, expected):
    assert combat(levels).level == expected


def test_levels_to_next_combat_from_fresh():
    nxt = levels_to_next_combat({})
    assert nxt["Strength"] is not None and nxt["Magic"] is not None


# ---------- builds ----------
def test_every_build_yaml_loads_and_has_a_source():
    builds = load_builds()
    assert {"one-defence-pure", "berserker", "obsidian-mauler", "skill-pure", "main"} <= set(builds)
    assert all(b.source.startswith("https://oldschool.runescape.wiki/") for b in builds.values())


def test_find_build_by_alias():
    assert find_build("zerker").id == "berserker"
    with pytest.raises(BuildError):
        find_build("does-not-exist")


def test_invalid_yaml_is_rejected(tmp_path: Path):
    (tmp_path / "bad.yaml").write_text(
        "id: bad\nnames: [x]\nf2p_possible: true\nsource: s\nwiki_build: w\n"
        "summary: s\nlimits: {Defense: {max: 1, kind: hard}}\n"
    )
    with pytest.raises(BuildError, match="unknown skill"):
        load_builds(tmp_path)


def test_violations():
    assert violations(find_build("1 def pure"), {"Defence": 5}) == ["Defence 5 > 1 (hard)"]
    assert violations(find_build("1 def pure"), {"Defence": None}) == []


# ---------- XP guard ----------
DS1 = QuestFacts(
    "Dragon Slayer I", True, True, rewards=[("fixed", ["Strength"], 18650, 1), ("fixed", ["Defence"], 18650, 1)]
)
ISLES = QuestFacts(
    "The Fremennik Isles", True, True, rewards=[("choice", ["Attack", "Defence", "Hitpoints", "Strength"], 10000, 2)]
)
MM1 = QuestFacts(
    "Monkey Madness I",
    True,
    True,
    rewards=[("choice_bundle", ["Attack", "Defence", "Hitpoints", "Strength"], 35000, 1)],
)
FAIRYTALE = QuestFacts(
    "Fairytale I - Growing Pains",
    True,
    True,
    rewards=[("fixed", ["Farming"], 3500, 1), ("fixed", ["Attack"], 2000, 1), ("fixed", ["Magic"], 1000, 1)],
)


def test_dragon_slayer_blocks_a_one_defence_pure_but_fits_a_zerker():
    assert judge_one(DS1, find_build("1 def pure"), FRESH_XP).status == "blocked"
    zerker = judge_one(DS1, find_build("zerker"), FRESH_XP)
    assert zerker.status == "fits" and zerker.locked_xp == {"Defence": 18650}


def test_zerker_close_to_45_cannot_take_dragon_slayer():
    xp = {**FRESH_XP, "Defence": xp_for_level(44)}
    assert judge_one(DS1, find_build("zerker"), xp).status == "blocked"


def test_single_skill_lamp_is_safe_if_you_choose():
    v = judge_one(ISLES, find_build("1 def pure"), FRESH_XP)
    assert v.status == "safe_if_choose" and "not Defence" in v.reasons[0]


def test_package_choice_is_a_risk():
    assert judge_one(MM1, find_build("1 def pure"), FRESH_XP).status == "risk"


def test_fairytale_attack_xp_blocks_strength_pure_only():
    assert judge_one(FAIRYTALE, find_build("1 def pure"), FRESH_XP).status == "safe"
    assert judge_one(FAIRYTALE, find_build("strength pure"), FRESH_XP).status == "blocked"


def test_skill_requirement_above_limit_blocks():
    quest = QuestFacts("Needs Defence", True, True, skill_reqs=[("Defence", 30, False)])
    assert judge_one(quest, find_build("1 def pure"), FRESH_XP).status == "blocked"


def test_blocked_prerequisite_blocks_the_whole_chain():
    after = QuestFacts("After DS1", True, True, prerequisites=["Dragon Slayer I"])
    verdicts = {
        v.quest: v for v in judge_all({"Dragon Slayer I": DS1, "After DS1": after}, find_build("1 def pure"), FRESH_XP)
    }
    assert verdicts["After DS1"].status == "blocked"
    assert "prerequisite Dragon Slayer I is blocked" in verdicts["After DS1"].reasons


def test_missing_rewards_template_is_unknown_not_safe():
    assert judge_one(QuestFacts("Miniquest", True, False), find_build("1 def pure"), FRESH_XP).status == "unknown"


def test_f2p_filter_keeps_only_free_quests():
    quests = {"Members": QuestFacts("Members", True, True), "Free": QuestFacts("Free", False, True)}
    assert [v.quest for v in judge_all(quests, find_build("main"), FRESH_XP, members=False)] == ["Free"]
