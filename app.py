"""DraftDay: league in, tested draft list out.

One page. No accounts, no AI calls, no database. Copy lives in copy.md.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import time
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request

from engine.blend import load_pool, load_rows, skill_compression
from engine.rank import build_list
from engine import sim as sim_mod
from engine import news as news_mod
from engine import addplayer
from engine.draft_tips import DRAFT_TIPS

app = Flask(__name__)
ROOT = Path(__file__).resolve().parent

DEFAULT_ROSTER = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "WR/RB/TE": 1,
                  "K": 1, "DEF": 1}
PRESETS = {"standard": 0.0, "half_ppr": 0.5, "ppr": 1.0}


# ── copy.md ─────────────────────────────────────────────────────────────
_copy_cache: tuple[float, dict] | None = None


def load_copy() -> dict[str, str]:
    global _copy_cache
    path = ROOT / "copy.md"
    mtime = path.stat().st_mtime
    if _copy_cache and _copy_cache[0] == mtime:
        return _copy_cache[1]
    blocks: dict[str, str] = {}
    slug = None
    buf: list[str] = []
    for line in path.read_text().splitlines():
        m = re.match(r"^##\s+([\w-]+)\s*$", line)
        if m:
            if slug:
                blocks[slug] = "\n".join(buf).strip()
            slug, buf = m.group(1), []
        elif slug is not None:
            buf.append(line)
    if slug:
        blocks[slug] = "\n".join(buf).strip()
    _copy_cache = (mtime, blocks)
    return blocks


# ── league parsing ──────────────────────────────────────────────────────
def _int(v, default, lo, hi):
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def league_from(payload: dict) -> dict:
    teams = _int(payload.get("teams"), 12, 4, 20)
    roster = {}
    for slot, n in (payload.get("roster") or DEFAULT_ROSTER).items():
        n = _int(n, 0, 0, 6)
        if n:
            roster[slot.upper()] = n
    if not roster:
        roster = dict(DEFAULT_ROSTER)
    scoring = dict(payload.get("scoring") or {})
    preset = payload.get("preset")
    if preset in PRESETS and "ppr" not in scoring:
        scoring["ppr"] = PRESETS[preset]
    return {
        "teams": teams,
        "slot": _int(payload.get("slot"), 1, 1, teams),
        "bench": _int(payload.get("bench"), 6, 0, 12),
        "style": payload.get("style", "snake"),
        "roster": roster,
        "scoring": scoring,
        "last_weight": _int(payload.get("last_weight"), 50, 0, 100),
    }


def _extras_from(payload: dict) -> list[dict]:
    """Session-added players (engine.addplayer rows) riding along on the
    request. The app is otherwise stateless server-side — order, roster,
    scoring all round-trip through the client on every call — so a
    searched-and-added player follows the same pattern rather than needing
    a server-side session store just for this."""
    extras = payload.get("extra_players")
    return extras if isinstance(extras, list) else []


# ── routes ──────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html", c=load_copy(),
                           copy_json=json.dumps(load_copy()))


@app.route("/api/search-players", methods=["POST"])
def search_players():
    payload = request.get_json(force=True, silent=True) or {}
    query = (payload.get("query") or "").strip()
    L = league_from(payload)
    pool = load_pool(L["scoring"], L["last_weight"],
                     extra_rows=_extras_from(payload))
    existing = {p["name"] for p in pool}
    hits = addplayer.search(query, existing)
    return jsonify({"players": hits})


@app.route("/api/add-player", methods=["POST"])
def add_player():
    payload = request.get_json(force=True, silent=True) or {}
    name = (payload.get("name") or "").strip()
    if not name:
        return jsonify({"error": "No player name given."}), 400

    row = addplayer.build_row(name)
    if not row:
        return jsonify({"error": f"Couldn't find {name} on an NFL roster "
                                 f"with enough 2025 games to build a "
                                 f"projection from, or he plays a position "
                                 f"(K/DEF) this can't add automatically."}), 400

    # Confirmation IS the ESPN roster match build_row() just did — position
    # and team came from ESPN's own roster data, not the searching visitor's
    # say-so — so the shared file write happens right here, not behind a
    # separate human review step.
    shared = addplayer.append_to_shared_pool(row)

    return jsonify({"row": row, "addedToSharedPool": shared})


@app.route("/api/build", methods=["POST"])
def build():
    payload = request.get_json(force=True, silent=True) or {}
    L = league_from(payload)
    extras = _extras_from(payload)
    pool = load_pool(L["scoring"], L["last_weight"], extra_rows=extras)
    if not pool:
        return jsonify({"error": "No player data."}), 500
    compression = skill_compression(load_rows(extras), L["scoring"])
    order = build_list(pool, L["teams"], L["roster"], L["bench"],
                       compression=compression)

    news_mod.attach(order)

    tips = _draft_tips(order, L)

    players = []
    for p in order:
        row = {k: p.get(k) for k in
               ("rank", "name", "pos", "team", "bye", "pts", "proj",
                "actual", "vorp", "posRank", "dropToNext", "measured", "sd")}
        row["news"] = [{"headline": a["headline"], "url": a["url"],
                        "published": a["published"], "direct": a["direct"]}
                       for a in (p.get("news") or [])]
        players.append(row)

    roster_preview = sim_mod.likely_roster(
        order, pool, L["slot"], L["teams"], L["roster"], L["bench"],
        L["style"])

    return jsonify({
        "league": L,
        "players": players,
        "tips": tips,
        "rosterPreview": roster_preview,
        "asOf": time.strftime("%Y-%m-%d"),
    })


def _draft_tips(order: list[dict], L: dict) -> dict:
    """Three sections of players worth a look before you draft.

    These are REAL draft-strategy notes (sleeper calls, bust warnings, value
    picks) hand-collected from actual 2026 fantasy analysis via live web
    search — see data/draft_tips.py for the full sourcing note and every
    article URL. This is deliberately NOT the same thing as the per-player
    ESPN news attached elsewhere on the page (injuries, transactions): that
    is real-time team news, this is draft-day strategy content, and they
    answer different questions.

    Sections, by where a player actually lands on THIS list:
      * top      — your highest-ranked players; a "top12" strategy note from
                   a real article if one exists for him, otherwise none
      * value    — mid-list players with a real bust warning or undervalued
                   call attached (this is where nearly all of that content
                   naturally lands: warnings matter most for players people
                   are actually paying up for)
      * deep     — late-list players with a real sleeper call attached

    A rank band with no sourced players in it is returned empty rather than
    padded with unrelated players — 8 real deep sleepers beats 20 with 12
    made up to hit a round number.
    """
    rank_of = {p["name"]: p["rank"] for p in order}
    pos_of = {p["name"]: p["pos"] for p in order}
    starters = sum(L["roster"].values())
    depth = starters * L["teams"]

    def entry(t):
        return {"name": t["name"], "pos": pos_of.get(t["name"], "?"),
                "rank": rank_of.get(t["name"]), "note": t["note"],
                "source": t["source"], "url": t["url"]}

    top, value, deep = [], [], []
    for t in DRAFT_TIPS:
        r = rank_of.get(t["name"])
        if r is None:
            continue          # this league's blend/scoring dropped him, or
                              # he's not in the projection pool at all
        if t["kind"] == "top12" and r <= 20:
            top.append(entry(t))
        elif t["kind"] in ("bust", "undervalued"):
            value.append(entry(t))
        elif t["kind"] == "sleeper":
            deep.append(entry(t))

    top.sort(key=lambda e: e["rank"])
    value.sort(key=lambda e: e["rank"])
    deep.sort(key=lambda e: e["rank"])

    # Fill out "top" with plain best-available (no sourced note) up to 20,
    # since only ~11 players will ever have a real top-12 strategy note —
    # the section is "your top 20," the note is a bonus when one exists.
    have = {e["name"] for e in top}
    for p in order[:20]:
        if p["name"] not in have:
            top.append({"name": p["name"], "pos": p["pos"], "rank": p["rank"],
                        "note": None, "source": None, "url": None})
    top.sort(key=lambda e: e["rank"])

    return {"top": top[:20], "value": value[:20], "deep": deep[:20]}


def _pool_too_shallow(pool: list[dict], L: dict) -> str | None:
    """A league the pool cannot fill would produce numbers that measure the
    data, not the roster. Returns an error string, or None if fine.

    Two separate ways a pool can be too shallow, and both matter:

      1. Not enough of ONE position for every team's starting slot (e.g. a
         superflex league needing 24 QBs from a 20-QB pool). Checked first.
      2. Not enough players in TOTAL to fill every team's full roster,
         starters plus bench. A 14-team league with a 7-man bench needs
         14 * (9 + 7) = 224 draft slots; this pool has 186 players. Before
         this check existed, the draft loop would simply run out of players
         partway through and silently skip the remaining picks — every
         team's roster (including "yours") came back short, with whole
         position groups like DEF or WR/RB/TE flex missing entirely, and
         nothing in the response said why.
    """
    have: dict[str, int] = {}
    for p in pool:
        have[p["pos"]] = have.get(p["pos"], 0) + 1
    for pos, need in L["roster"].items():
        if "/" not in pos and have.get(pos, 0) < need * L["teams"]:
            return f"Not enough {pos}s in the player pool for {L['teams']} teams."

    starters = sum(n for pos, n in L["roster"].items())
    needed = L["teams"] * (starters + L["bench"])
    if len(pool) < needed:
        return (f"This league needs {needed} drafted players "
                f"({L['teams']} teams x {starters + L['bench']} roster "
                f"spots) and the player pool only has {len(pool)}. Lower "
                f"the bench size or team count, or this league is too deep "
                f"for the data behind this tool.")
    return None


def _order_from(payload: dict, pool: list[dict], L: dict) -> list[dict]:
    by_name = {p["name"]: p for p in pool}
    names = payload.get("order") or []
    order = [by_name[n] for n in names if n in by_name]
    if order:
        return order
    # Degenerate path: a caller reached this endpoint without an explicit
    # order (normally /api/build already ran and the client is echoing its
    # result back). Rebuilding needs the same compression signal /api/build
    # used, or K/DEF placement would silently differ between the two.
    compression = skill_compression(load_rows(_extras_from(payload)),
                                    L["scoring"])
    return build_list(pool, L["teams"], L["roster"], L["bench"],
                      compression=compression)


@app.route("/api/simulate", methods=["POST"])
def simulate():
    """Streams NDJSON progress frames while the season simulation runs.

    200 trials of the actual computation finishes in well under a second,
    which reads as broken rather than fast when the button says "simulating
    your season" and the number just appears. Split into batches so the
    client can show real, incrementally-accumulating progress (not a fake
    timed progress bar) and pace it to feel like the real work it is.
    """
    payload = request.get_json(force=True, silent=True) or {}
    L = league_from(payload)
    pool = load_pool(L["scoring"], L["last_weight"],
                     extra_rows=_extras_from(payload))
    order = _order_from(payload, pool, L)

    err = _pool_too_shallow(pool, L)
    if err:
        return jsonify({"error": err}), 400

    total = int(os.environ.get("DD_TRIALS", 200))
    batches = 20
    per = max(1, total // batches)

    def gen():
        acc = {"wins": [], "points": [], "injuredStarts": 0, "waiverAdds": 0}
        done = 0
        frame = 0
        while done < total:
            n = min(per, total - done)
            frame += 1
            raw = sim_mod.run_raw(order, pool, L["slot"], L["teams"],
                                  L["roster"], L["bench"], L["style"],
                                  trials=n, seed=4000 + frame)
            acc["wins"] += raw["wins"]
            acc["points"] += raw["points"]
            acc["injuredStarts"] += raw["injuredStarts"]
            acc["waiverAdds"] += raw["waiverAdds"]
            done += n

            frame_out = {"done": done, "total": total}
            if done >= total:
                summary = sim_mod.summarize(acc)
                if not summary:
                    frame_out["error"] = "Simulation failed."
                else:
                    summary["tips"] = _season_tips(order, pool, L)
                    frame_out.update(summary)
            yield json.dumps(frame_out) + "\n"

    return Response(gen(), mimetype="application/x-ndjson",
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no"})


@app.route("/api/roster-preview", methods=["POST"])
def roster_preview():
    """The likely starting roster for the CURRENT (possibly reordered) list,
    without running a season. Cheap enough to call on every reorder."""
    payload = request.get_json(force=True, silent=True) or {}
    L = league_from(payload)
    pool = load_pool(L["scoring"], L["last_weight"],
                     extra_rows=_extras_from(payload))
    order = _order_from(payload, pool, L)

    err = _pool_too_shallow(pool, L)
    if err:
        return jsonify({"error": err}), 400

    preview = sim_mod.likely_roster(order, pool, L["slot"], L["teams"],
                                    L["roster"], L["bench"], L["style"])
    return jsonify({"roster": preview})


@app.route("/api/availability", methods=["POST"])
def availability():
    """For your current list order: how often does each of your next ~40
    targets last to the pick where taking him is a real decision?

    This is the "can I get this guy" check, separate from the season
    simulation — it answers a DRAFT question (will he be there), not a
    SEASON question (does my roster win). Reordering the list changes this
    answer; it never changes how opponents draft.
    """
    payload = request.get_json(force=True, silent=True) or {}
    L = league_from(payload)
    pool = load_pool(L["scoring"], L["last_weight"],
                     extra_rows=_extras_from(payload))
    order = _order_from(payload, pool, L)

    err = _pool_too_shallow(pool, L)
    if err:
        return jsonify({"error": err}), 400

    trials = int(os.environ.get("DD_AVAIL_TRIALS", 300))
    result = sim_mod.check_availability(
        order, pool, L["slot"], L["teams"], L["roster"], L["bench"],
        L["style"], trials=trials)
    return jsonify({"players": result})


def _season_tips(order: list[dict], pool: list[dict], L: dict) -> list[str]:
    """Management notes computed from THIS league's settings, not generic
    advice. Each one only appears when the underlying condition is true."""
    tips = []
    starters = sum(L["roster"].values())
    top = order[:starters]

    byes: dict[int, int] = {}
    for p in top:
        if p.get("bye"):
            byes[p["bye"]] = byes.get(p["bye"], 0) + 1
    worst = max(byes.items(), key=lambda kv: kv[1], default=None)
    if worst and worst[1] >= 3:
        tips.append(f"{worst[1]} of your likely starters share a week "
                    f"{worst[0]} bye. Plan that week early.")

    # Players the league likely leaves undrafted: everyone past draft depth
    # on the default board, best first.
    drafted_depth = L["teams"] * (starters + L["bench"])
    by_adp = sorted(pool, key=lambda p: p.get("adp") or 999)
    likely_free = sorted((p for p in by_adp[drafted_depth:]
                          if p["pos"] in ("QB", "RB", "WR", "TE")),
                         key=lambda p: -p["pts"])[:5]
    if likely_free:
        tips.append("Likely available on waivers: "
                    + ", ".join(p["name"] for p in likely_free) + ".")

    # Defense/DST streaming: real signal from this league's own scoring.
    # pa_pg (points allowed per game) is a SEASON AVERAGE, not a weekly
    # opponent schedule — there is no weekly matchup data in this dataset, so
    # this identifies defenses worth streaming off waivers in general, not a
    # specific week's matchup. Only surfaces if the league actually rewards
    # defense meaningfully (skip in point-stingy leagues where it won't move
    # a roster decision).
    def_scoring_spread = None
    defs = sorted((p for p in pool if p["pos"] == "DEF" and p.get("pa_pg")),
                  key=lambda p: p["pa_pg"])
    if len(defs) >= 6:
        stingy = defs[:3]           # allow the fewest points -> best streams
        def_scoring_spread = max(p["pts"] for p in pool if p["pos"] == "DEF") \
            - min(p["pts"] for p in pool if p["pos"] == "DEF")
        if def_scoring_spread and def_scoring_spread >= 40:
            names = ", ".join(p["name"] for p in stingy)
            tips.append(f"Defense swings about {round(def_scoring_spread)} "
                        f"points here between the best and worst options. "
                        f"{names} allow the fewest points per game, so "
                        f"stream whichever one is on waivers and has a "
                        f"soft matchup that week, rather than locking in "
                        f"one defense all season.")

    # Superflex / two-QB: streaming a QB off waivers is a much bigger swing
    # than in a one-QB league, worth calling out because it changes behavior.
    if L["roster"].get("QB", 0) >= 2:
        tips.append("You start two quarterbacks. QB depth on waivers thins "
                    "out fast, so grab a second startable one in the last "
                    "few rounds if your list did not already.")

    return tips


@app.route("/api/export", methods=["POST"])
def export():
    payload = request.get_json(force=True, silent=True) or {}
    L = league_from(payload)
    pool = load_pool(L["scoring"], L["last_weight"],
                     extra_rows=_extras_from(payload))
    order = _order_from(payload, pool, L)

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["rank", "name", "team", "position"])   # Yahoo's import format
    for i, p in enumerate(order, 1):
        w.writerow([i, p["name"], p["team"], p["pos"]])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition":
                             "attachment; filename=draftday.csv"})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5051, debug=False)
