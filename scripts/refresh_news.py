"""Refresh data/player_news.csv from ESPN's public news endpoints.

Run this yourself, whenever you want fresher news (daily is reasonable
during the season) -- it is never called by the running app:

    python scripts/refresh_news.py

Fetches all 32 teams' recent articles from ESPN, de-duplicates them, and
overwrites data/player_news.csv. Commit the updated CSV and redeploy (or
just push -- Render picks up the new file on the next build) to put the
refreshed news in front of users.

This lives here instead of inside engine/news.py's request path on purpose:
a live fetch across 32 teams is slow and network-dependent enough that
running it inline, per page-load, was taking the whole site down on a slow
host. Doing it here means the worst case is a slow *script run on your own
machine*, not a 500 for every visitor.
"""
from __future__ import annotations

import csv
import json
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.news import CSV_PATH, FIELDS   # noqa: E402

TEAMS_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams"
NEWS_URL = ("https://site.api.espn.com/apis/site/v2/sports/football/nfl/"
            "news?limit=50&team={id}")
TIMEOUT = 20


def _get(url: str):
    # No User-Agent header. ESPN 403s a descriptive UA ("RankMyDraft/1.0") and
    # also 403s a spoofed browser one, while urllib's own default passes. Since
    # dressing up as a browser is both blocked and dishonest, send nothing and
    # let urllib identify itself.
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.load(r)


def _team_ids() -> list[str]:
    d = _get(TEAMS_URL)
    return [t["team"]["id"] for t in d["sports"][0]["leagues"][0]["teams"]]


def main() -> int:
    print("fetching team list...")
    try:
        ids = _team_ids()
    except Exception as e:
        print(f"could not reach ESPN: {e}")
        return 1
    print(f"{len(ids)} teams")

    def one(tid):
        try:
            return _get(NEWS_URL.format(id=tid)).get("articles", [])
        except Exception as e:
            print(f"  team {tid} failed: {e}")
            return []

    seen: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for batch in ex.map(one, ids):
            for a in batch:
                seen[str(a.get("id"))] = a

    rows = []
    for a in seen.values():
        athletes = [c.get("description") for c in a.get("categories", [])
                    if c.get("type") == "athlete" and c.get("description")]
        rows.append({
            "id": str(a.get("id")),
            "headline": a.get("headline", ""),
            "description": (a.get("description") or "")[:300],
            "published": a.get("published", ""),
            "url": (a.get("links", {}).get("web", {}) or {}).get("href", ""),
            "athletes": "|".join(athletes),
        })

    if not rows:
        print("no articles found, leaving existing CSV untouched")
        return 1

    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    print(f"wrote {len(rows)} articles to {CSV_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
