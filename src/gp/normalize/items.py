"""Equipment requirements from the item page's lead section (the wiki has no structured field - DECISIONS.md D3).

The wiki phrases them in many ways, e.g.
    "The rune scimitar is a scimitar requiring level 40 [[Attack]] to wield."
    "It can only be wielded by players who have 60 [[Attack]] and have completed the [[Monkey Madness I]] quest."
    "Players must have level 45 [[Defence]] and also have completed the quest [[The Fremennik Trials]] ...
     in order to wear it."
Rule: in the lead section (text before the first heading), a sentence that mentions wield/wear/equip and contains
"<level> [[Skill]]" pairs is a requirement sentence. Quests named in the same sentence after "complet..." are
quest requirements. Negations ("lack a Defence requirement") are skipped. The sentence is kept as evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from gp.normalize.quests import SKILLS

_SKILL_RE = "|".join(SKILLS)
EQUIP_VERB = re.compile(r"\b(?:wield|wear|equip)", re.I)
NEGATION = re.compile(r"\b(?:lack|no|without|not require|does not)\b[^.]{0,40}requirement", re.I)
LEVEL_SKILL = re.compile(r"(\d{1,2})\s*\[\[(?:[^\]|]*\|)?(" + _SKILL_RE + r")\]\]", re.I)
LINK = re.compile(r"\[\[([^\]|#]+)(?:\|[^\]]*)?\]\]")
GENERIC_LINKS = {"quest", "quests", "miniquest", "quest series", "achievement diary", "achievement diaries"}


@dataclass
class EquipRequirements:
    skills: list[tuple[str, int]] = field(default_factory=list)
    quests: list[str] = field(default_factory=list)
    evidence: str = ""


def lead_section(wikitext: str) -> str:
    text = (wikitext or "").split("\n==", 1)[0]
    for _ in range(3):  # strip (nested) templates such as infoboxes
        text = re.sub(r"\{\{[^{}]*\}\}", " ", text)
    return re.sub(r"<ref[^>]*>.*?</ref>|<ref[^>]*/>|'''|''", "", text, flags=re.S)


def _plain(sentence: str) -> str:
    return re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]+)\]\]", r"\1", sentence).strip()


def equip_requirements(wikitext: str) -> EquipRequirements:
    for sentence in re.split(r"(?<=[.!?])\s+", lead_section(wikitext)):
        if not EQUIP_VERB.search(sentence) or NEGATION.search(sentence):
            continue
        pairs = LEVEL_SKILL.findall(sentence)
        if not pairs:
            continue
        after_complete = sentence[m.end() :] if (m := re.search(r"complet\w*", sentence, re.I)) else ""
        quests = [
            q.strip()
            for q in LINK.findall(after_complete)
            if q.strip() not in SKILLS and q.strip().lower() not in GENERIC_LINKS
        ]
        return EquipRequirements([(s.capitalize(), int(lvl)) for lvl, s in pairs], quests, _plain(sentence))
    return EquipRequirements()
