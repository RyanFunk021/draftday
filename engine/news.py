"""Player news from ESPN's public endpoints.

No API key, no registration. ESPN exposes news per team; fetching all 32 gives
~800 unique articles against ~50 from the general feed, which is the difference
between covering a third of a draft board and covering most of it.

Two ways an article gets attached to a player:

  1. ESPN's own athlete tags (categories[].type == "athlete"). Authoritative
     when present — it's ESPN asserting the article is about that person.
  2. Full-name match in headline/description. Catches what the tags miss.

Name matching uses FULL names only. Last-name matching sounds better until
"Brown" attaches Cleveland Browns coverage to Amon-Ra St. Brown, and there are
enough Williamses and Johnsons in the NFL to make it actively wrong.

Coverage runs ~60% of a typical board. The rest genuinely have no recent news,
and saying so is more useful than inventing something.
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

CACHE = Path(__file__).resolve().parent.parent / "data" / "news_cache.json"
CACHE_TTL = 3 * 3600          # news moves, but not minute to minute
TEAMS_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams"
NEWS_URL = ("https://site.api.espn.com/apis/site/v2/sports/football/nfl/"
            "news?limit=50&team={id}")
TIMEOUT = 20

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
    return [t["team"]["id"]
            for t in d["sports"][0]["leagues"][0]["teams"]]


def fetch_articles(force: bool = False) -> list[dict]:
    """All recent NFL articles, de-duplicated. Cached to disk."""
    if not force and CACHE.exists():
        try:
            blob = json.loads(CACHE.read_text())
            if time.time() - blob.get("fetched", 0) < CACHE_TTL:
                return blob["articles"]
        except (json.JSONDecodeError, KeyError):
            pass   # corrupt cache is not worth failing over

    def one(tid):
        try:
            return _get(NEWS_URL.format(id=tid)).get("articles", [])
        except Exception:
            return []      # one dead team must not sink the whole fetch

    try:
        ids = _team_ids()
    except Exception:
        ids = []

    seen: dict[str, dict] = {}
    if ids:
        with ThreadPoolExecutor(max_workers=8) as ex:
            for batch in ex.map(one, ids):
                for a in batch:
                    seen[str(a.get("id"))] = a

    articles = [{
        "id": str(a.get("id")),
        "headline": a.get("headline", ""),
        "description": (a.get("description") or "")[:300],
        "published": a.get("published", ""),
        "url": (a.get("links", {}).get("web", {}) or {}).get("href", ""),
        "athletes": [c.get("description") for c in a.get("categories", [])
                     if c.get("type") == "athlete" and c.get("description")],
    } for a in seen.values()]

    if articles:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(
            {"fetched": time.time(), "articles": articles}))
        return articles

    # Fetch failed (ESPN unreachable, blocked, or rate-limited). Stale news
    # beats no news: the panel says how old it is, so a reader can judge it.
    # Returning [] here would empty every player's news the moment the TTL
    # lapsed, while a perfectly usable cache sat on disk.
    try:
        return json.loads(CACHE.read_text())["articles"]
    except (OSError, json.JSONDecodeError, KeyError):
        return []


def cache_age_hours() -> float | None:
    """How old the cached news is, or None if never fetched."""
    try:
        blob = json.loads(CACHE.read_text())
        return (time.time() - blob.get("fetched", 0)) / 3600
    except (OSError, json.JSONDecodeError):
        return None


def newest_article() -> str:
    """Publish date of the most recent article held, YYYY-MM-DD."""
    try:
        blob = json.loads(CACHE.read_text())
        dates = [a.get("published", "") for a in blob.get("articles", [])]
        return max(d[:10] for d in dates if d) if any(dates) else ""
    except (OSError, json.JSONDecodeError, ValueError):
        return ""


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

    Never raises — news is an enhancement, and a ranking without it is still a
    ranking. A network failure leaves every player with an empty list.
    """
    try:
        articles = fetch_articles()
    except Exception:
        articles = []
    idx = index_by_player([p["name"] for p in players], articles, per_player)
    for p in players:
        p["news"] = idx.get(p["name"], [])
    return sum(1 for p in players if p["news"])
