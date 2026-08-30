"""Player pool: this year's projections blended with last year's results.

Both numbers are computed under the league's own scoring, then mixed by a
user-set weight. A player with no game log last year (rookies, mostly) keeps
his projection at any weight, because zero last year is an absence of data,
not a performance.

Kickers and defenses have no per-game logs here, so they also keep their
projection. The blend is a skill-position control.
"""
from __future__ import annotations

import csv
from pathlib import Path

from .scoring import season_points, DEFAULT_SCORING
from .gamelogs import fetch_weekly, weekly_stats, norm

DATA = Path(__file__).resolve().parent.parent / "data"

# Rows backfilled from a completed season (src=espn2025) are results, not
# forecasts. Discounting them keeps deep-league pool depth without letting a
# career year outrank a considered projection.
BACKFILL_DISCOUNT = 0.88

POS_SD_FALLBACK = {"QB": 7.5, "RB": 7.0, "WR": 7.5, "TE": 5.0,
                   "K": 3.5, "DEF": 4.5}


def _f(v) -> float:
    try:
        return float(str(v).strip() or 0)
    except ValueError:
        return 0.0


SKILL_COMPRESSION_BASELINE = {**DEFAULT_SCORING, "ppr": 0.5}   # half-PPR


def load_rows(extra_rows: list[dict] | None = None) -> list[dict]:
    """Raw projections.csv rows (plus any session-added extras), before
    scoring. Shared by load_pool() and skill_compression() so both work
    from the identical row set without reading the file twice."""
    rows = list(csv.DictReader((DATA / "projections.csv").open()))
    if extra_rows:
        have = {(r.get("name") or "").strip().lower() for r in rows}
        rows += [r for r in extra_rows
                if (r.get("name") or "").strip().lower() not in have]
    return rows


def skill_compression(rows: list[dict], scoring: dict) -> float:
    """How much this league's scoring shrinks skill-position points versus
    the site's own default league (half-PPR, standard yardage), as a single
    ratio: 1.0 = no change, 0.7 = this league scores skill positions at 70%
    of the default. The baseline is half-PPR specifically because that is
    what a new list defaults to, not bare 0-PPR — otherwise every ordinary
    half-PPR league would show as "inflated" relative to a reference nobody
    actually plays under.

    Kicker and defense scoring never depends on yardage rates, so a format
    that halves yardage-derived points (like HFL) leaves K/DEF exactly
    where they were while every skill player around them scores less. That
    is a real value shift worth reacting to (see rank.py), and this ratio
    is the cheap way to detect it: total skill-position points under this
    scoring divided by the same rows under the baseline, no second network
    call or game-log refetch needed since it only touches the projection
    numbers already sitting in `rows`.
    """
    skill_rows = [r for r in rows
                 if (r.get("pos") or "").upper().strip()
                 not in ("K", "DEF", "DST", "D/ST")]
    if not skill_rows:
        return 1.0
    this_total = sum(season_points(r, scoring) for r in skill_rows)
    base_total = sum(season_points(r, SKILL_COMPRESSION_BASELINE)
                     for r in skill_rows)
    return this_total / base_total if base_total else 1.0


def load_pool(scoring: dict, last_weight: int = 50,
              extra_rows: list[dict] | None = None) -> list[dict]:
    """Every draftable player, scored and blended. last_weight is 0-100.

    extra_rows: same shape as a projections.csv row (engine.addplayer
    builds them), for players a user searched for and added to just THIS
    session. They go through the identical scoring path as everyone else —
    same blend, same weekly-variance lookup, same backfill discount if
    src=espn2025 — so a session-added player is not a second-class entry,
    just one that did not (yet, or ever) make it into the shared file.
    """
    rows = load_rows(extra_rows)
    names = [r["name"] for r in rows if r.get("name")]
    weeks = fetch_weekly(names)
    w = max(0, min(100, int(last_weight))) / 100.0

    pool = []
    for r in rows:
        name = (r.get("name") or "").strip()
        pos = (r.get("pos") or "").upper().strip()
        if not name or not pos:
            continue

        proj = season_points(r, scoring)
        if (r.get("src") or "").startswith("espn"):
            proj *= BACKFILL_DISCOUNT

        mine = [wk for wk in weeks.get(norm(name), [])
                if wk.get("_season") == 2025]
        actual = sum(season_points(wk, scoring) for wk in mine) if mine else None

        pts = (w * actual + (1 - w) * proj) if actual is not None else proj

        stats = weekly_stats(weeks.get(norm(name), []), scoring)
        sd = stats["sd"] if stats else POS_SD_FALLBACK.get(pos, 6.0)

        pool.append({
            "name": name, "pos": pos,
            "team": (r.get("team") or "").strip(),
            "bye": int(_f(r.get("bye"))) or 0,
            "adp": _f(r.get("adp")) or 999.0,
            "pts": round(pts, 1),
            "proj": round(proj, 1),
            "actual": round(actual, 1) if actual is not None else None,
            "sd": round(sd, 1),
            "measured": bool(stats),
            # Points allowed per game, defenses only. Not used in scoring
            "pa_pg": _f(r.get("pa_pg")) if pos == "DEF" else None,
        })
    return pool
