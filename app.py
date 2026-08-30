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

    news_hits = news_mod.attach(order)

    # 20 to consider: best-ranked players with a DIRECT headline mention.
    tips = []
    for p in order:
        direct = next((a for a in p.get("news") or [] if a.get("direct")),
                      None)
        if direct:
            tips.append({"name": p["name"], "pos": p["pos"],
                         "headline": direct["headline"]})
        if len(tips) >= 20:
            break

    players = []
    for p in order:
        row = {k: p.get(k) for k in
               ("rank", "name", "pos", "team", "bye", "pts", "proj",
                "actual", "vorp")}
        # One tidbit per player is the ask; the top article is the tidbit.
        arts = p.get("news") or []
        row["tidbit"] = arts[0]["headline"] if arts else None
        players.append(row)
    return jsonify({
        "league": L,
        "players": players,
        "tips": tips,
        "newsCount": news_hits,
        "asOf": time.strftime("%Y-%m-%d"),
    })


@app.route("/api/simulate", methods=["POST"])
def simulate():
    payload = request.get_json(force=True, silent=True) or {}
    L = league_from(payload)
    pool = load_pool(L["scoring"], L["last_weight"])
    by_name = {p["name"]: p for p in pool}

    names = payload.get("order") or []
    order = [by_name[n] for n in names if n in by_name]
    if not order:
        order = build_list(pool, L["teams"], L["roster"], L["bench"])

    # A league the pool cannot fill would produce a number that measures the
    # data, not the roster. Say so instead.
    have: dict[str, int] = {}
    for p in pool:
        have[p["pos"]] = have.get(p["pos"], 0) + 1
    for pos, need in L["roster"].items():
        if "/" not in pos and have.get(pos, 0) < need * L["teams"]:
            return jsonify({"error": f"Not enough {pos}s in the player pool "
                                     f"for {L['teams']} teams."}), 400

    trials = int(os.environ.get("DD_TRIALS", 150))
    result = sim_mod.run(order, pool, L["slot"], L["teams"], L["roster"],
                         L["bench"], L["style"], trials=trials)
    if not result:
        return jsonify({"error": "Simulation failed."}), 500

    result["tips"] = _season_tips(order, pool, L)
    return jsonify(result)


def _season_tips(order: list[dict], pool: list[dict], L: dict) -> list[str]:
    """League-specific notes, computed rather than written."""
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
    # on the default board, best first. The pool is shallow, so this can be
    # a short list; short and real beats padded.
    drafted_depth = L["teams"] * (starters + L["bench"])
    by_adp = sorted(pool, key=lambda p: p.get("adp") or 999)
    likely_free = sorted((p for p in by_adp[drafted_depth:]
                          if p["pos"] in ("QB", "RB", "WR", "TE")),
                         key=lambda p: -p["pts"])[:5]
    if likely_free:
        tips.append("Likely available on waivers: "
                    + ", ".join(p["name"] for p in likely_free) + ".")
    return tips


@app.route("/api/export", methods=["POST"])
def export():
    payload = request.get_json(force=True, silent=True) or {}
    L = league_from(payload)
    pool = load_pool(L["scoring"], L["last_weight"])
    by_name = {p["name"]: p for p in pool}
    names = payload.get("order") or []
    order = [by_name[n] for n in names if n in by_name] or \
        build_list(pool, L["teams"], L["roster"], L["bench"])

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
