"""XP guard: which quests would push a locked skill past the build's limit?

For every quest, given the build's limits and the account's current XP (or a fresh account):

  blocked         fixed XP in a locked skill exceeds the remaining headroom, a skill requirement is above a limit,
                  or a prerequisite quest is itself blocked (the whole chain must be safe)
  risk            random XP or a choice between packages (DECISIONS.md D4) can land in a locked skill beyond headroom
  safe_if_choose  a lamp/tome lists a locked skill but also other options - fine if you pick one of those
  fits            fixed XP in a locked skill that still fits under the limit (e.g. a zerker below 45 Defence)
  safe            no XP in locked skills
  unknown         rewards could not be read (no template / unclassified line touching a locked skill)

Pure function over rows loaded from the store, so it is unit-testable without a database.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from gp.rules.builds import Build

SEVERITY = {"blocked": 5, "risk": 4, "unknown": 3, "safe_if_choose": 2, "fits": 1, "safe": 0}


@dataclass
class QuestFacts:
    name: str
    members: bool | None
    rewards_parsed: bool
    skill_reqs: list[tuple[str, int, bool]] = field(default_factory=list)  # (skill, level, boostable)
    prerequisites: list[str] = field(default_factory=list)
    rewards: list[tuple[str, list[str], int | None, int]] = field(default_factory=list)  # (kind, skills, xp, quantity)
    source_url: str = ""


@dataclass
class Verdict:
    quest: str
    status: str
    reasons: list[str]
    locked_xp: dict[str, int]  # fixed XP that lands in locked skills
    source_url: str


@dataclass
class _Findings:
    status: str = "safe"
    reasons: list[str] = field(default_factory=list)

    def add(self, status: str, reason: str) -> None:
        if SEVERITY[status] > SEVERITY[self.status]:
            self.status = status
        self.reasons.append(reason)


def _judge_variable_reward(
    kind: str, skills: list[str], *, amount: int | None, total: int, build: Build, xp: dict[str, int], out: _Findings
) -> None:
    """Rewards whose skill is not fixed: random XP, package choices, lamps/tomes, unclassified lines."""
    locked = [s for s in skills if s in build.limits]
    if not locked:
        return
    if kind in ("random", "choice_bundle"):
        over = [s for s in locked if amount is None or xp.get(s, 0) + total > build.limits[s].max_xp]
        if over:
            upto = f" (up to {total:,})" if amount else ""
            out.add("risk", f"{kind.replace('_', ' ')} can give {', '.join(over)} XP{upto}")
    elif kind == "choice":
        others = [s for s in skills if s not in build.limits]
        if others or skills == ["Any"]:
            pick = ", ".join(others) if others else "a skill you are not locking"
            out.add("safe_if_choose", f"lamp/tome: pick {pick} - not {', '.join(locked)}")
        else:
            out.add("blocked", f"choosable XP only in locked skills: {', '.join(locked)}")
    elif kind == "unparsed":
        out.add("unknown", f"reward text mentions {', '.join(locked)} but could not be classified")


def judge_one(q: QuestFacts, build: Build, xp: dict[str, int]) -> Verdict:
    """xp: current experience per skill (fresh-account values when unknown)."""
    out = _Findings()
    for skill, level, boostable in q.skill_reqs:
        if (lim := build.limits.get(skill)) and level > lim.max:
            out.add("blocked", f"requires {level} {skill} (limit {lim.max}){' - boostable' if boostable else ''}")

    locked_xp: dict[str, int] = {}
    for kind, skills, amount, quantity in q.rewards:
        total = (amount or 0) * max(quantity, 1)
        if kind == "fixed":
            if skills and skills[0] in build.limits:
                locked_xp[skills[0]] = locked_xp.get(skills[0], 0) + total
        else:
            _judge_variable_reward(kind, skills, amount=amount, total=total, build=build, xp=xp, out=out)

    for skill, gained in locked_xp.items():
        lim = build.limits[skill]
        after = xp.get(skill, 0) + gained
        if after > lim.max_xp:
            out.add("blocked", f"+{gained:,} {skill} XP -> over the {lim.max} {skill} limit")
        else:
            out.add("fits", f"+{gained:,} {skill} XP still fits under {lim.max} ({lim.max_xp - after:,} XP to spare)")

    if not q.rewards_parsed:
        out.add("unknown", "no rewards template on the wiki page - check manually")
    return Verdict(q.name, out.status, out.reasons, locked_xp, q.source_url)


def judge_all(quests: dict[str, QuestFacts], build: Build, xp: dict[str, int], members: bool = True) -> list[Verdict]:
    """Every quest, with prerequisites propagated: a quest is never safer than the quests it requires."""
    own = {name: judge_one(q, build, xp) for name, q in quests.items()}
    final: dict[str, Verdict] = {}

    def resolve(name: str, stack: tuple[str, ...] = ()) -> Verdict:
        if name in final:
            return final[name]
        verdict = own[name]
        for pre in quests[name].prerequisites:
            if pre not in own or pre in stack:
                continue
            pre_v = resolve(pre, (*stack, name))
            if SEVERITY[pre_v.status] >= SEVERITY["risk"] and SEVERITY[pre_v.status] > SEVERITY[verdict.status]:
                verdict = Verdict(
                    name,
                    pre_v.status,
                    [*verdict.reasons, f"prerequisite {pre} is {pre_v.status}"],
                    verdict.locked_xp,
                    verdict.source_url,
                )
        final[name] = verdict
        return verdict

    out = [resolve(n) for n in sorted(own)]
    if not members:
        out = [v for v in out if quests[v.quest].members is False]
    return sorted(out, key=lambda v: (-SEVERITY[v.status], v.quest))
