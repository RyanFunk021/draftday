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

from engine.blend import load_pool
from engine.rank import build_list
from engine import sim as sim_mod
from engine import news as news_mod

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


# ── routes ──────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html", c=load_copy(),
                           copy_json=json.dumps(load_copy()))


@app.route("/api/build", methods=["POST"])
def build():
    L = league_from(request.get_json(force=True, silent=True) or {})
    pool = load_pool(L["scoring"], L["last_weight"])
    if not pool:
        return jsonify({"error": "No player data."}), 500
    order = build_list(pool, L["teams"], L["roster"], L["bench"])

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


def _draft_tips(order: list[dict], L: dict) -> list[dict]:
    """20 players worth a look before you draft.

    Two kinds, both computed from real signals rather than written text:

      * news — best-ranked players with a direct ESPN headline this week
      * value — players sitting well past their ADP on THIS list, i.e. this
        league's scoring rates them below the market's consensus price, so
        they're a plausible late add or a player to not reach for
    """
    news_tips = []
    for p in order:
        direct = next((a for a in p.get("news") or [] if a.get("direct")),
                      None)
        if direct:
            news_tips.append({"kind": "news", "name": p["name"],
                              "pos": p["pos"], "headline": direct["headline"],
                              "url": direct["url"]})
        if len(news_tips) >= 12:
            break

    starters = sum(L["roster"].values())
    value_tips = []
    for p in order[:starters * L["teams"]]:
        adp = p.get("adp") or 999
        gap = adp - p["rank"]
        if gap >= 15:      # ranked well ahead of where the market drafts him
            value_tips.append({"kind": "value", "name": p["name"],
                               "pos": p["pos"],
                               "headline": f"Ranked {p['rank']} here, usual "
                                          f"ADP {round(adp)} — a market gap "
                                          f"in your league's scoring.",
                               "url": None})
    value_tips.sort(key=lambda t: t["name"])

    out = news_tips[:12]
    remaining = 20 - len(out)
    out += value_tips[:remaining]
    return out[:20]


def _pool_too_shallow(pool: list[dict], L: dict) -> str | None:
    """A league the pool cannot fill would produce numbers that measure the
    data, not the roster. Returns an error string, or None if fine."""
    have: dict[str, int] = {}
    for p in pool:
        have[p["pos"]] = have.get(p["pos"], 0) + 1
    for pos, need in L["roster"].items():
        if "/" not in pos and have.get(pos, 0) < need * L["teams"]:
            return f"Not enough {pos}s in the player pool for {L['teams']} teams."
    return None


def _order_from(payload: dict, pool: list[dict], L: dict) -> list[dict]:
    by_name = {p["name"]: p for p in pool}
    names = payload.get("order") or []
    order = [by_name[n] for n in names if n in by_name]
    return order or build_list(pool, L["teams"], L["roster"], L["bench"])


@app.route("/api/simulate", methods=["POST"])
def simulate():
    payload = request.get_json(force=True, silent=True) or {}
    L = league_from(payload)
    pool = load_pool(L["scoring"], L["last_weight"])
    order = _order_from(payload, pool, L)

    err = _pool_too_shallow(pool, L)
    if err:
        return jsonify({"error": err}), 400

    trials = int(os.environ.get("DD_TRIALS", 150))
    result = sim_mod.run(order, pool, L["slot"], L["teams"], L["roster"],
                         L["bench"], L["style"], trials=trials)
    if not result:
        return jsonify({"error": "Simulation failed."}), 500

    result["tips"] = _season_tips(order, pool, L)
    return jsonify(result)


@app.route("/api/roster-preview", methods=["POST"])
def roster_preview():
    """The likely starting roster for the CURRENT (possibly reordered) list,
    without running a season. Cheap enough to call on every reorder."""
    payload = request.get_json(force=True, silent=True) or {}
    L = league_from(payload)
    pool = load_pool(L["scoring"], L["last_weight"])
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
    pool = load_pool(L["scoring"], L["last_weight"])
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
                        f"{names} allow the fewest points per game — stream "
                        f"whichever one is on waivers and has a soft matchup "
                        f"that week, rather than locking in one defense all "
                        f"season.")

    # Superflex / two-QB: streaming a QB off waivers is a much bigger swing
    # than in a one-QB league, worth calling out because it changes behavior.
    if L["roster"].get("QB", 0) >= 2:
        tips.append("You start two quarterbacks. QB depth on waivers thins "
                    "out fast — grab a second startable one in the last "
                    "few rounds if your list did not already.")

    return tips


@app.route("/api/export", methods=["POST"])
def export():
    payload = request.get_json(force=True, silent=True) or {}
    L = league_from(payload)
    pool = load_pool(L["scoring"], L["last_weight"])
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
