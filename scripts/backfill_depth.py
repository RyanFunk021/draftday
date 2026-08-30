"""One-off: widen the shared player pool so deep leagues (14 teams, big
bench) clear engine.blend's total-pool-size guard.

At 186 players, 14 teams x 15 roster spots (210 needed) already failed. This
pulls real 2025 ESPN game logs for every skill-position player on an NFL
roster who ISN'T already in data/projections.csv, keeps the ones with a real
in-season sample, and appends them through the exact same path the site's
own "search and add a player" feature uses (engine.addplayer.build_row) --
same pro-rating, same 1.35x cap on a short season, same src=espn2025
marking, same discount when scored. Nothing here is a special case; it is
the existing single-player mechanism run in a loop.

Kickers and defenses are already at their real-world ceiling (14 each, one
per NFL team) and cannot be deepened this way -- build_row() only handles
skill positions, because K/DEF score off a completely different stat shape
a game log cannot produce.

    python scripts/backfill_depth.py [--min-games 8] [--target 90] [--dry-run]
"""
from __future__ import annotations

import argparse
import csv
import io
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.gamelogs import athlete_index, norm, _weeks   # noqa: E402
from engine import addplayer                                # noqa: E402

CSV_PATH = addplayer.CSV_PATH
SEASON = addplayer.SEASON
GAMES = addplayer.GAMES
MAX_SCALE = addplayer.MAX_SCALE
STAT_FIELDS = addplayer.STAT_FIELDS


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-games", type=int, default=8,
                    help="games needed before a 2025 sample counts as a "
                         "real contributor rather than a cup of coffee")
    ap.add_argument("--target", type=int, default=90,
                    help="how many new rows to add, ranked by 2025 volume")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rows = list(csv.DictReader(CSV_PATH.open()))
    header = list(rows[0].keys())
    for col in ("src",):
        if col not in header:
            header.append(col)

    have = {norm(r["name"]) for r in rows if r.get("name")}
    idx = athlete_index()
    if not idx:
        print("could not reach ESPN")
        return 1

    cand = [(k, v) for k, v in idx.items()
            if v["pos"] in addplayer.SKILL_POS and k not in have]
    print(f"{len(cand)} skill-position players on rosters, not yet in the pool")

    def fetch(kv):
        _, v = kv
        weeks = _weeks(v["id"], SEASON)
        return v, weeks

    with ThreadPoolExecutor(max_workers=16) as ex:
        found = list(ex.map(fetch, cand))

    real = [(v, w) for v, w in found if len(w) >= args.min_games]
    print(f"{len(real)} had {args.min_games}+ games in {SEASON}")

    # Rank by season passing/rush/rec yardage combined, a simple volume
    # proxy -- the point is picking real contributors first, not a precise
    # ranking (VORP/points get computed properly once these are scored
    # through the normal pipeline at request time).
    def volume(w):
        return sum(sum(g.get(k, 0) for g in w)
                  for k in ("py", "ry", "recy"))

    real.sort(key=lambda vw: -volume(vw[1]))
    picked = real[: args.target]

    bye_of = addplayer._bye_by_team()
    added = []
    for v, weeks in picked:
        scale = min(GAMES / len(weeks), MAX_SCALE)
        totals = {k: sum(w.get(k, 0) for w in weeks) for k in STAT_FIELDS}
        row = {c: "" for c in header}
        row.update({
            "name": v["name"], "pos": v["pos"], "team": v["team"],
            "bye": bye_of.get(v["team"], ""), "adp": "999",
            "note": f"{SEASON} actuals over {len(weeks)} games, "
                    f"pro-rated to {GAMES}",
            "src": f"espn{SEASON}",
        })
        for k in STAT_FIELDS:
            row[k] = str(round(totals[k] * scale))
        added.append(row)
        print(f"  {v['name']:<24} {v['pos']:<3} {len(weeks):>2}g  "
              f"py={round(totals['py']*scale):>5} "
              f"ry={round(totals['ry']*scale):>5} "
              f"recy={round(totals['recy']*scale):>5}")

    print(f"\nadding {len(added)} rows "
          f"({len(rows)} players -> {len(rows) + len(added)})")
    if args.dry_run:
        print("dry run, nothing written")
        return 0

    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=header)
    w.writeheader()
    for r in rows + added:
        w.writerow({k: r.get(k, "") for k in header})
    CSV_PATH.write_text(buf.getvalue())
    print(f"wrote {CSV_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
