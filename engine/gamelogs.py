"""Per-week game logs from ESPN, for measured weekly variance.

Season totals cannot answer "how many weeks will this player win for me".
A receiver who goes 88 yards every week and one who alternates 23 and 165
have the same season line and very different value, because fantasy weeks
are won by margins of a few points and lost to a single quiet game.

This module fetches each player's week-by-week box scores from ESPN's public
gamelog endpoint (no key, same host the news feature already uses), converts
each week to fantasy points under a league's own scoring, and reports the
mean and standard deviation of that weekly distribution.

Two seasons are pulled where available. Recent form matters more, so the
current season is weighted more heavily, but one season of 17 games is a
thin sample for a standard deviation and the extra year steadies it.

What this does NOT do is predict. The output is a description of how varied
a player HAS been. Using it as a forecast assumes usage and role carry over,
which for most players is roughly true and for some is badly wrong.
"""
from __future__ import annotations

import json
import re
import statistics
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .scoring import season_points

DATA = Path(__file__).resolve().parent.parent / "data"
CACHE = DATA / "gamelog_cache.json"
CACHE_TTL = 7 * 24 * 3600      # last season's games do not change

ROSTER_CACHE = DATA / "roster_cache.json"
ROSTER_CACHE_TTL = 12 * 3600   # rosters shift (waivers, practice squad moves)

TEAMS_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams"
ROSTER_URL = ("https://site.api.espn.com/apis/site/v2/sports/football/nfl/"
              "teams/{id}/roster")
GAMELOG_URL = ("https://site.web.api.espn.com/apis/common/v3/sports/football/"
               "nfl/athletes/{pid}/gamelog?season={season}")
TIMEOUT = 25

# Current season first. Weight favours recent usage without discarding the
# older sample, which is what keeps a 17-game standard deviation from being
# dominated by one outlier afternoon.
SEASONS = (2025, 2024)
SEASON_WEIGHT = {2025: 2.0, 2024: 1.0}

# A player needs enough games for a standard deviation to mean anything.
MIN_GAMES = 6

# ESPN gamelog stat names -> the keys engine.scoring expects. Anything absent
# from a given player's log is simply zero for that week.
STAT_MAP = {
    "passingYards": "py",
    "passingTouchdowns": "ptd",
    "interceptions": "ints",
    "rushingYards": "ry",
    "rushingTouchdowns": "rtd",
    "receivingYards": "recy",
    "receivingTouchdowns": "rectd",
    "receptions": "rec",
    "fumblesLost": "fum",
}

# Suffixes and punctuation differ between sources ("Ja'Marr" vs "Ja&#39;Marr",
# "Kenneth Walker III"). Matching on a normalised form catches most of it.
_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\.?$", re.I)
_PUNCT = re.compile(r"[^a-z ]")


def _get(url: str):
    # No User-Agent, for the reason documented in engine/news.py: ESPN 403s a
    # descriptive one and also 403s a spoofed browser one.
    with urllib.request.urlopen(urllib.request.Request(url), timeout=TIMEOUT) as r:
        return json.load(r)


def norm(name: str) -> str:
    """Normalised name for matching across sources."""
    n = (name or "").lower().replace("&#39;", "'").replace("'", "")
    n = _PUNCT.sub(" ", n)
    n = _SUFFIX.sub("", n.strip())
    return " ".join(n.split())


def _num(v) -> float:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def athlete_index(force: bool = False) -> dict[str, dict]:
    """Every rostered NFL player, keyed by normalised name.

    Built from the 32 team rosters rather than a search endpoint, because
    search returns nothing useful and the roster route is the one already
    proven to work here.

    Cached to disk: this is 32 live HTTP calls, which is fine once per
    gamelog fetch but far too slow to run per keystroke behind a live player
    search. A stale roster (someone traded last night) is a much smaller
    problem than a search box that takes several seconds to respond.
    """
    if not force and ROSTER_CACHE.exists():
        try:
            blob = json.loads(ROSTER_CACHE.read_text())
            if time.time() - blob.get("fetched", 0) < ROSTER_CACHE_TTL:
                return blob["index"]
        except (OSError, json.JSONDecodeError):
            pass

    try:
        teams = _get(TEAMS_URL)["sports"][0]["leagues"][0]["teams"]
    except Exception:
        try:      # network down: stale roster beats no roster
            return json.loads(ROSTER_CACHE.read_text()).get("index", {})
        except (OSError, json.JSONDecodeError):
            return {}
    ab_of = {t["team"]["id"]: t["team"]["abbreviation"] for t in teams}
    ids = [t["team"]["id"] for t in teams]

    def one(tid):
        try:
            r = _get(ROSTER_URL.format(id=tid))
        except Exception:
            return []          # one unreachable team must not sink the rest
        out = []
        for grp in r.get("athletes", []):
            items = grp.get("items", grp if isinstance(grp, list) else [])
            for a in items:
                if a.get("id") and a.get("displayName"):
                    out.append({
                        "id": str(a["id"]),
                        "name": a["displayName"],
                        "pos": ((a.get("position") or {}).get("abbreviation")
                                or "").upper(),
                        "team": ab_of.get(tid, ""),
                    })
        return out

    index: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for batch in ex.map(one, ids):
            for a in batch:
                index.setdefault(norm(a["name"]), a)

    if index:
        ROSTER_CACHE.parent.mkdir(parents=True, exist_ok=True)
        ROSTER_CACHE.write_text(json.dumps({"fetched": time.time(),
                                            "index": index}))
    return index


def _weeks(pid: str, season: int) -> list[dict]:
    """One player's weekly stat rows for a season, keyed for engine.scoring."""
    try:
        g = _get(GAMELOG_URL.format(pid=pid, season=season))
    except Exception:
        return []
    names = g.get("names") or []
    if not names:
        return []
    rows = []
    for st in g.get("seasonTypes", []):
        # Postseason weeks are not fantasy weeks in most leagues.
        if "Regular" not in (st.get("displayName") or ""):
            continue
        for cat in st.get("categories", []):
            for ev in cat.get("events", []):
                stats = ev.get("stats") or []
                if len(stats) != len(names):
                    continue
                raw = dict(zip(names, stats))
                # A "did not play" week is not a zero-point performance, it is
                # an absence. Counting it as zero would make every injured
                # player look wildly inconsistent rather than simply missing.
                if all(_num(raw.get(k)) == 0 for k in
                       ("rushingYards", "receivingYards", "passingYards",
                        "receptions", "rushingAttempts")):
                    continue
                rows.append({dst: _num(raw.get(src))
                             for src, dst in STAT_MAP.items()})
    return rows


def fetch_weekly(names: list[str], force: bool = False) -> dict[str, list[dict]]:
    """Weekly stat rows per player name. Cached to disk for a week."""
    if not force and CACHE.exists():
        try:
            blob = json.loads(CACHE.read_text())
            if time.time() - blob.get("fetched", 0) < CACHE_TTL:
                have = blob.get("weeks", {})
                if all(norm(n) in have for n in names):
                    return have
        except (OSError, json.JSONDecodeError):
            pass

    index = athlete_index()
    if not index:
        try:      # network down: stale data beats none
            return json.loads(CACHE.read_text()).get("weeks", {})
        except (OSError, json.JSONDecodeError):
            return {}

    wanted = {norm(n): n for n in names}

    def one(key):
        hit = index.get(key)
        if not hit:
            return key, []
        rows = []
        for season in SEASONS:
            for r in _weeks(hit["id"], season):
                r["_season"] = season
                rows.append(r)
        return key, rows

    weeks: dict[str, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for key, rows in ex.map(one, wanted):
            weeks[key] = rows

    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps({"fetched": time.time(), "weeks": weeks}))
    return weeks


def weekly_stats(weeks: list[dict], scoring: dict) -> dict | None:
    """Mean and standard deviation of weekly points under a league's rules.

    Scoring is applied per week rather than to a season total, so a league's
    own rules shape the spread: full PPR lifts a high-catch receiver's floor,
    while a touchdown-heavy league widens nearly everyone's.
    """
    pts, wts = [], []
    for w in weeks:
        pts.append(season_points(w, scoring))
        wts.append(SEASON_WEIGHT.get(w.get("_season"), 1.0))
    if len(pts) < MIN_GAMES:
        return None

    total = sum(wts)
    mean = sum(p * wt for p, wt in zip(pts, wts)) / total
    var = sum(wt * (p - mean) ** 2 for p, wt in zip(pts, wts)) / total
    sd = var ** 0.5
    return {
        "games": len(pts),
        "mean": round(mean, 1),
        "sd": round(sd, 1),
        "floor": round(statistics.quantiles(pts, n=10)[0], 1) if len(pts) >= 10
                 else round(min(pts), 1),
        "ceiling": round(statistics.quantiles(pts, n=10)[8], 1) if len(pts) >= 10
                   else round(max(pts), 1),
    }
