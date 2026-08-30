"""VORP, replacement level, and streaming discounts.

The HFL version hardcoded REPLACEMENT_RANK = {"QB": 14, "RB": 21, ...} for a
specific 14-team roster. Here it's DERIVED from league size and roster slots,
which is the whole reason the tool generalizes: the same starting lineup in a
10-team league has completely different replacement levels than in a 14-team one.
"""
from __future__ import annotations

# How flex slots distribute across the positions eligible for them. A W/R/T
# flex is taken by a WR far more often than a TE, so splitting it evenly would
# understate WR replacement level and overstate TE.
FLEX_SPLIT = {
    "WR/RB":     {"WR": 0.60, "RB": 0.40},
    "WR/RB/TE":  {"WR": 0.55, "RB": 0.33, "TE": 0.12},
    "WR/TE":     {"WR": 0.80, "TE": 0.20},
    "QB/WR/RB/TE": {"QB": 0.70, "WR": 0.15, "RB": 0.12, "TE": 0.03},  # superflex
}

# Positions replaceable off waivers week to week. Raw VORP overstates their
# DRAFT value: you don't need to roster a good one, you need to roster a
# streamable one. Multiplier applied to VORP.
STREAM_DISCOUNT = {"DEF": 0.35, "K": 0.60}


def replacement_ranks(teams: int, roster: dict) -> dict:
    """Derive replacement rank per position from league size and roster slots.

    roster maps slot name -> count, e.g.
        {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "WR/RB/TE": 1, "K": 1, "DEF": 1}

    Returns {"QB": 12, "RB": 30, ...} — the rank at which a position becomes
    freely available, which is what VORP measures against.
    """
    starters: dict[str, float] = {}
    for slot, count in (roster or {}).items():
        slot = slot.upper().strip()
        if not count:
            continue
        if slot in FLEX_SPLIT:
            for pos, share in FLEX_SPLIT[slot].items():
                starters[pos] = starters.get(pos, 0.0) + count * share
        else:
            starters[slot] = starters.get(slot, 0.0) + count

    ranks = {}
    for pos, per_team in starters.items():
        # Every team fills its starting slots before the position dries up.
        ranks[pos] = max(1, round(per_team * teams))
    return ranks


def _replacement_points(group: list[dict], rank: int) -> float:
    """Points scored by the replacement-level player at this position.

    When fewer players are listed than the replacement rank — routine for K and
    DEF, where feeds list ~10-12 — using the worst LISTED player massively
    inflates VORP across the position. Extrapolate the tail instead.
    """
    if not group:
        return 0.0
    if len(group) >= rank:
        return group[rank - 1]["pts"]
    if len(group) >= 3:
        slope = (group[0]["pts"] - group[-1]["pts"]) / max(len(group) - 1, 1)
        return max(group[-1]["pts"] - slope * (rank - len(group)), 0.0)
    return group[-1]["pts"]



def add_vorp(players: list[dict], teams: int, roster: dict) -> list[dict]:
    """Attach vorp and posRank to every player. Mutates and returns."""
    ranks = replacement_ranks(teams, roster)

    by_pos: dict[str, list[dict]] = {}
    for p in players:
        by_pos.setdefault(p["pos"], []).append(p)

    for pos, group in by_pos.items():
        group.sort(key=lambda x: -x["pts"])
        repl = _replacement_points(group, ranks.get(pos, len(group)))
        for i, p in enumerate(group):
            p["vorp"] = round(p["pts"] - repl, 1)
            p["posRank"] = i + 1
            # How much you give up by taking the NEXT player at this position
            # instead. This replaces the old tier-break marker: it answers the
            # same question ("is there a cliff here?") as a plain number the
            # reader can weigh, rather than a line whose threshold was arbitrary.
            nxt = group[i + 1] if i + 1 < len(group) else None
            p["dropToNext"] = round(p["pts"] - nxt["pts"], 1) if nxt else 0.0

        # Streaming discount.
        disc = STREAM_DISCOUNT.get(pos)
        if disc:
            for p in group:
                p["vorp"] = round(p["vorp"] * disc, 1)

    players.sort(key=lambda p: -p["vorp"])
    for i, p in enumerate(players):
        p["rank"] = i + 1
    return players
