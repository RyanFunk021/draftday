"""Search for a player not in the pool, and add him.

Two separate things happen when someone adds a player, and they are not the
same operation:

  1. He goes into THIS visitor's session pool immediately, built from his
     2025 ESPN game log pro-rated to a full season (same mechanism as the
     superflex QB backfill in scripts/fetch_qbs.py) — this is a real,
     sourced number, just not a hand-built 2026 forecast like the rest of
     the pool. He is scored, ranked and draftable right away.

  2. He is ALSO offered for the SHARED pool every future visitor loads from
     (data/projections.csv), but only after his position and team are
     confirmed against ESPN's own roster data — the same authoritative
     source used to build the pool in the first place, not the searching
     visitor's say-so. A typo'd name or a made-up player never resolves to
     an ESPN roster entry, so it can never reach the shared file. This is
     the gate: confirmation IS the ESPN roster match, not a human queue.

Skill positions only (QB/RB/WR/TE). Kickers and defenses score off a
completely different stat shape (field goals by distance; points allowed by
tier) that a game-log backfill cannot produce, and search-adding either is
rare enough not to be worth a second special case.
"""
from __future__ import annotations

import csv
import io
import json
import time
from pathlib import Path

from .gamelogs import athlete_index, norm, _weeks

DATA = Path(__file__).resolve().parent.parent / "data"
CSV_PATH = DATA / "projections.csv"

SEASON = 2025
GAMES = 17
MAX_SCALE = 1.35        # see scripts/fetch_qbs.py in the sibling project for
                        # why a partial season is capped, not fully stretched
MIN_GAMES = 3            # below this, a "season pace" is close to a guess

SKILL_POS = {"QB", "RB", "WR", "TE"}

STAT_FIELDS = ("py", "ptd", "ry", "rtd", "recy", "rectd", "rec", "ints", "fum")


def _bye_by_team() -> dict[str, str]:
    """Every team's bye week, read off the existing pool.

    Bye is a team property, not a player one — every team already has
    several real rows in the CSV, so this is a real lookup, not a guess.
    """
    out: dict[str, str] = {}
    try:
        for r in csv.DictReader(CSV_PATH.open()):
            t, b = (r.get("team") or "").strip(), (r.get("bye") or "").strip()
            if t and b and t not in out:
                out[t] = b
    except OSError:
        pass
    return out


def search(query: str, existing_names: set[str], limit: int = 8) -> list[dict]:
    """Players on NFL rosters matching `query`, not already in the pool."""
    q = norm(query)
    if len(q) < 2:
        return []
    idx = athlete_index()
    have = {norm(n) for n in existing_names}
    hits = []
    for key, a in idx.items():
        # Only positions build_row() can actually turn into a scored row.
        # Showing a kicker in search results who then fails to add (with a
        # confusing error) is worse than not showing him at all — and team
        # defenses have no individual roster entry in this data at all,
        # ESPN's per-team roster feed lists PLAYERS, not the defense as a
        # unit, so DEF was never a real search hit to begin with.
        if key in have or a["pos"] not in SKILL_POS:
            continue
        if q in key:
            hits.append(a)
    # Names that START with the query read as more relevant than ones that
    # merely contain it ("mahomes" typed should surface Mahomes before
    # anyone whose last name happens to contain those letters).
    hits.sort(key=lambda a: (not norm(a["name"]).startswith(q), a["name"]))
    return hits[:limit]


def build_row(name: str) -> dict | None:
    """A projections.csv-shaped row for one player, from his 2025 game log.

    Returns None if he can't be found on a roster, isn't a skill position,
    or has too thin a sample to pro-rate honestly.
    """
    idx = athlete_index()
    hit = idx.get(norm(name))
    if not hit or hit["pos"] not in SKILL_POS:
        return None

    weeks = _weeks(hit["id"], SEASON)
    if len(weeks) < MIN_GAMES:
        return None

    totals = {k: sum(w.get(k, 0) for w in weeks) for k in STAT_FIELDS}
    scale = min(GAMES / len(weeks), MAX_SCALE)

    bye = _bye_by_team().get(hit["team"], "")

    row = {
        "name": hit["name"], "pos": hit["pos"], "team": hit["team"],
        "bye": bye, "adp": "999",
        "note": f"{SEASON} actuals over {len(weeks)} games, "
                f"pro-rated to {GAMES}",
        "src": f"espn{SEASON}",
    }
    for k in STAT_FIELDS:
        row[k] = str(round(totals[k] * scale))
    return row


def append_to_shared_pool(row: dict) -> bool:
    """Write a row to the LIVE, shared projections.csv every future visitor
    loads from — only ever called after build_row() has already confirmed
    the player against ESPN's roster, which is the trust gate. Returns False
    (and writes nothing) if he's somehow already there.
    """
    try:
        existing = list(csv.DictReader(CSV_PATH.open()))
    except OSError:
        return False
    header = list(existing[0].keys()) if existing else list(row.keys())
    for col in row:
        if col not in header:
            header.append(col)

    if any(norm(r.get("name", "")) == norm(row["name"]) for r in existing):
        return False

    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=header)
    w.writeheader()
    for r in existing:
        w.writerow({k: r.get(k, "") for k in header})
    w.writerow({k: row.get(k, "") for k in header})
    CSV_PATH.write_text(buf.getvalue())
    return True
