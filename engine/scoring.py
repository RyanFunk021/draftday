"""League-agnostic scoring.

The HFL board hardcoded its scoring as module constants. Here the same math
takes a settings dict, so any league's rules produce that league's points.

Yardage is expressed as YARDS PER POINT rather than points per yard because
that is how every fantasy platform states it ("1 point per 25 passing yards"),
and because it makes the halved-yardage leagues like HFL read naturally
(50 yd/pt instead of 0.02 pt/yd).
"""
from __future__ import annotations

import math

# Standard-league defaults. Every key here is overridable per league.
DEFAULT_SCORING = {
    "pass_yd_per_pt": 25.0,
    "rush_yd_per_pt": 10.0,
    "recv_yd_per_pt": 10.0,
    "pass_td": 4.0,
    "rush_td": 6.0,
    "recv_td": 6.0,
    "ppr": 0.0,          # 0 = standard, 0.5 = half, 1.0 = full
    "int_thrown": -2.0,
    "fumble_lost": -2.0,
    "fg_0_39": 3.0,
    "fg_40_49": 4.0,
    "fg_50": 5.0,
    "pat": 1.0,
    # D/ST: points-allowed tiers as (upper_bound_inclusive, points).
    "dst_tiers": [(0, 10), (6, 7), (13, 4), (20, 1), (27, 0), (34, -1), (999, -4)],
    "dst_sack": 1.0,
    "dst_turnover": 2.0,
    "dst_td": 6.0,
}

# Weekly spread in points allowed by a defense. Integrating the tier table over
# this spread (rather than reading the tier its season average lands in) is what
# keeps 16.5 and 18.0 ppg from collapsing into the same bucket.
DST_WEEKLY_SD = 9.0
SEASON_GAMES = 17


def _f(row, key) -> float:
    """Float from a CSV-ish row, blank/garbage -> 0."""
    v = row.get(key)
    if v is None:
        return 0.0
    v = str(v).strip()
    if not v:
        return 0.0
    try:
        return float(v)
    except ValueError:
        return 0.0


def dst_points_per_game(pa_pg: float, scoring: dict) -> float:
    """Expected D/ST points per game given an average points-allowed rate.

    Integrates the league's tier table over a normal spread of weekly outcomes.
    """
    tiers = scoring.get("dst_tiers") or DEFAULT_SCORING["dst_tiers"]
    total = 0.0
    norm = 0.0
    for pa in range(0, 60):
        z = (pa - pa_pg) / DST_WEEKLY_SD
        w = math.exp(-0.5 * z * z)
        norm += w
        for cap, pts in tiers:
            if pa <= cap:
                total += w * pts
                break
    return total / norm if norm else 0.0


def season_points(row, scoring: dict) -> float:
    """Project a player's season fantasy points under this league's scoring."""
    s = {**DEFAULT_SCORING, **(scoring or {})}
    pos = (row.get("pos") or "").upper().strip()

    if pos in ("DEF", "DST", "D/ST"):
        base = dst_points_per_game(_f(row, "pa_pg"), s) * SEASON_GAMES
        # Sacks/turnovers/TDs are season totals when the feed supplies them.
        return (base
                + _f(row, "sacks") * s["dst_sack"]
                + _f(row, "turnovers") * s["dst_turnover"]
                + _f(row, "dst_td") * s["dst_td"])

    if pos == "K":
        return (_f(row, "fg39") * s["fg_0_39"]
                + _f(row, "fg40") * s["fg_40_49"]
                + _f(row, "fg50") * s["fg_50"]
                + _f(row, "pat") * s["pat"])

    return (_f(row, "py") / s["pass_yd_per_pt"]
            + _f(row, "ptd") * s["pass_td"]
            + _f(row, "ry") / s["rush_yd_per_pt"]
            + _f(row, "rtd") * s["rush_td"]
            + _f(row, "recy") / s["recv_yd_per_pt"]
            + _f(row, "rectd") * s["recv_td"]
            + _f(row, "rec") * s["ppr"]
            + _f(row, "ints") * s["int_thrown"]
            + _f(row, "fum") * s["fumble_lost"])
