"""Blended pool -> a draft list that survives Yahoo autodraft.

Skill positions sort by edge over replacement (what you gain vs the best
free player at that position). Kickers and defenses are placed by round,
four deep and adjacent: opponents drafting the default list take them
around their ADP, so a two-deep list loses both before your pick comes.
That was measured, not guessed (18 of 36 test drafts finished with no
kicker at two-deep; one of 36 at four-deep).
"""
from __future__ import annotations

from .vorp import add_vorp

TARGET_ROUND_FRAC = {"K": 0.80, "DEF": 0.87}
KEEP = {"K": 4, "DEF": 4}


def build_list(pool: list[dict], teams: int, roster: dict,
               bench: int) -> list[dict]:
    add_vorp(pool, teams, roster)
    rounds = sum(roster.values()) + bench

    skill = [p for p in pool if p["pos"] not in TARGET_ROUND_FRAC]
    skill.sort(key=lambda p: -p.get("vorp", 0))
    out = list(skill)

    for pos, frac in TARGET_ROUND_FRAC.items():
        group = sorted([p for p in pool if p["pos"] == pos],
                       key=lambda p: -p["pts"])[: KEEP.get(pos, 4)]
        target = min(int(rounds * frac * teams), len(out))
        for i, p in enumerate(group):
            out.insert(min(target + i, len(out)), p)

    for i, p in enumerate(out, 1):
        p["rank"] = i
    return out
