"""Player news, read from a local CSV -- never fetched live during a request.

This used to call ESPN's public news endpoints inline, per request, which
sounds fine until the host's network is slow: even bounded and backgrounded,
a live fetch is still a live fetch, and on at least one production host it
was enough to blow past the WSGI server's own worker timeout and 500 the
whole page just to attach "recent news" nobody was blocking on. News is
explicitly an enhancement (see attach() below) -- it has no business being
able to take the page down.

data/player_news.csv is the source of truth now. It is NOT written by the
running app. Refresh it offline, whenever you want (daily is reasonable),
by running:

    python scripts/refresh_news.py

from your own machine, then commit the updated CSV. See that script for the
actual ESPN fetch logic (unchanged from before, just moved out of the
request path).

Two ways an article gets attached to a player:

  1. ESPN's own athlete tags, if the row's "athletes" column names them.
  2. Full-name match in headline/description. Catches what tags miss.

Name matching uses FULL names only. Last-name matching sounds better until
"Brown" attaches Cleveland Browns coverage to Amon-Ra St. Brown, and there
are enough Williamses and Johnsons in the NFL to make it actively wrong.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "player_news.csv"
FIELDS = ["id", "headline", "description", "published", "url", "athletes"]

# Words that signal an article actually matters for a fantasy decision, most
# consequential first. Used to rank a player's articles, not to filter them.
SIGNAL = [
    (3, re.compile(r"\b(torn|acl|out for the season|ir\b|injured reserve|"
                   r"surgery|suspend\w*|released|waived|retire\w*)", re.I)),
    (2, re.compile(r"\b(injur\w*|hamstring|ankle|knee|concussion|questionable|"
                   r"doubtful|did not practice|limited|trade\w*|starter|"
                   r"starting|depth chart|snap count)", re.I)),
    (1, re.compile(r"\b(practice|preseason|camp|role|target|carries|"
                   r"touches|red zone)", re.I)),
]


def load_articles() -> list[dict]:
    """Everything in the local CSV. Missing file just means no news yet."""
    if not CSV_PATH.exists():
        return []
    with CSV_PATH.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    articles = []
    for r in rows:
        articles.append({
            "id": r.get("id", ""),
            "headline": r.get("headline", ""),
            "description": r.get("description", ""),
            "published": r.get("published", ""),
            "url": r.get("url", ""),
            "athletes": [a.strip() for a in (r.get("athletes") or "").split("|")
                        if a.strip()],
        })
    return articles


def cache_age_hours() -> float | None:
    """Hours since player_news.csv was last written, or None if it's never
    been generated. Named cache_age_hours for compatibility with existing
    callers -- this file is refreshed offline, not by a live request cache,
    but "how stale is the news on screen" is the same question either way."""
    if not CSV_PATH.exists():
        return None
    import time
    return (time.time() - CSV_PATH.stat().st_mtime) / 3600


def newest_article() -> str:
    """Publish date of the most recent article held, YYYY-MM-DD."""
    articles = load_articles()
    dates = [a.get("published", "")[:10] for a in articles if a.get("published")]
    return max(dates) if dates else ""


def _score(article: dict) -> int:
    blob = article["headline"] + " " + article["description"]
    for weight, pat in SIGNAL:
        if pat.search(blob):
            return weight
    return 0


def index_by_player(names: list[str], articles: list[dict],
                    per_player: int = 3) -> dict[str, list[dict]]:
    """Map player name -> their most relevant recent articles."""
    hits: dict[str, dict[str, dict]] = {n: {} for n in names}
    name_set = set(names)

    for a in articles:
        blob = a["headline"] + " " + a["description"]

        # The HEADLINE is what makes an article about someone. A name in the
        # description only proves he was mentioned — ESPN's description is the
        # opening body text, so a Bengals game recap "about" Joe Burrow is
        # really about the backup who played. Headline match is the strong tier.
        for n in name_set:
            if n in a["headline"]:
                hits[n][a["id"]] = dict(a, _direct=True)
            elif n in blob:
                hits[n].setdefault(a["id"], dict(a, _direct=False))

        # ESPN's athlete tags are broader: a team roundup gets tagged with
        # every player mentioned anywhere in the body, which is how a Bengals
        # preseason story about Josh Johnson ends up filed under Joe Burrow.
        # Keep tag-only matches, but mark them so they rank below direct hits
        # and can be labelled as team context rather than player news.
        for tagged in a["athletes"]:
            if tagged in name_set and a["id"] not in hits[tagged]:
                hits[tagged][a["id"]] = dict(a, _direct=False)

    out = {}
    for n, found in hits.items():
        if not found:
            continue
        # Articles naming the player outrank team context; then by how
        # consequential the article is, then by recency.
        ranked = sorted(found.values(),
                        key=lambda a: (a["_direct"], _score(a), a["published"]),
                        reverse=True)
        out[n] = [{
            "headline": a["headline"],
            "published": a["published"][:10],
            "url": a["url"],
            "signal": _score(a),
            "direct": a["_direct"],
        } for a in ranked[:per_player]]
    return out


def attach(players: list[dict], per_player: int = 3) -> int:
    """Attach a `news` list to each player. Returns how many players got any.

    Never raises, never touches the network -- reads the local CSV only. A
    missing or empty file leaves every player with an empty list.
    """
    try:
        articles = load_articles()
    except Exception:
        articles = []
    idx = index_by_player([p["name"] for p in players], articles, per_player)
    for p in players:
        p["news"] = idx.get(p["name"], [])
    return sum(1 for p in players if p["news"])
