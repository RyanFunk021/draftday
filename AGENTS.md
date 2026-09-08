# DraftDay — agent orientation

One Flask app, stateless server-side, single page. No accounts, no database,
no AI calls at runtime. Turns a fantasy football league's settings into a
tested, downloadable pre-draft rank list.

Directory map — read the nested AGENTS.md before working in that folder:
- `engine/AGENTS.md` — the ranking/scoring/simulation pipeline (the actual product logic)
- `scripts/AGENTS.md` — offline maintenance scripts (never run by the live app)
- `static/AGENTS.md` — frontend (vanilla JS/CSS, no framework, no build step)
- `templates/AGENTS.md` — the one Jinja template and the copy.md text system
- `data/AGENTS.md` — CSV/JSON data files, their provenance, and refresh cadence

## Request flow (app.py)

1. `league_from(payload)` parses/clamps league settings (teams, roster slots,
   scoring, bench, style) from the client's JSON on EVERY request — there is
   no server-side session. The client's `cfg()` (`static/app.js`) resends the
   full league config, current list order, and any session-added extra
   players on every call.
2. `engine.blend.load_pool()` builds the scored player pool: reads
   `data/projections.csv` (+ any extras), blends this year's projection with
   last year's actuals under the league's own scoring, at a user-controlled
   weight.
3. `engine.rank.build_list()` sorts skill positions by VORP (`engine.vorp`)
   and inserts K/DEF at a fixed round-fraction (empirically tuned so
   opponents' autodraft doesn't snipe them first).
4. `engine.news.attach()` adds pre-fetched ESPN news per player (from a local
   CSV, never a live fetch during a request — see `data/AGENTS.md` for why).
5. `engine.sim` either previews a likely draft outcome (`likely_roster`),
   estimates how long a given player survives to your pick
   (`check_availability`), or plays out full simulated seasons
   (`run_raw`/`summarize`) against a modeled field of opponents (half "live"
   human-like drafters with reach/run/need behavior, half mechanical
   Yahoo-style autodraft). All three routes (`/api/roster-preview`,
   `/api/availability`, `/api/simulate`) call `app.py`'s `_exclude_gone()`
   first, which drops any player the live draft tracker has already logged
   as taken (`payload["gone_players"]`, sent by `static/app.js`'s `cfg()`)
   and adjusts `_pool_too_shallow()`'s depth requirement down by however
   many of those slots are already filled — without that adjustment the
   guard would start false-failing partway through a real draft, once fewer
   than a FULL draft's worth of players remain in the (now smaller) pool.
6. `/api/export` returns the final order as a Yahoo-import CSV.

## Key architectural facts worth knowing before changing anything

- **Fully stateless server.** Nothing about a visitor's league or list
  persists server-side between requests (`app.py`'s `_extras_from` docstring
  spells this out) — the client is the source of truth for league config and
  list order. Any new feature that needs to remember state across requests
  (see the improvement proposal below) breaks this assumption and needs a
  deliberate decision about where that state lives.
- **The player pool is shared, mutable, append-only.** `data/projections.csv`
  is written to at runtime by `engine.addplayer.append_to_shared_pool()` when
  a visitor searches-and-adds a real NFL player who isn't in the pool yet.
  This is the one place the "no database" claim gets an asterisk — the CSV
  *is* a lightweight shared database.
- **Everything is derived from league size and roster shape, not
  hardcoded.** Replacement rank (`engine.vorp.replacement_ranks`), K/DEF
  placement round, and draft-slot pick numbers all scale with
  `teams`/`roster`/`bench`. A prior single-league version of this tool
  (sibling `rankmydraft` project, referenced in several docstrings)
  hardcoded these; don't reintroduce that.
- **Measured, not guessed.** Several constants in `engine/rank.py` and
  `engine/sim.py` (K/DEF placement fraction, `FILL_REQUIRED_AFTER`,
  `LATE_POSITIONS`) exist because an earlier mechanic was tested against real
  draft outcomes and found wrong (e.g., a kicker landing at median opponent
  pick 69.5 against a real-world floor of ~190). If you touch these,
  re-measure — don't tune by intuition alone.
- **News and draft-strategy tips are both static snapshots**, never fetched
  live during a request. A prior version fetched ESPN news inline
  per-request and it 500'd production under slow network conditions. Refresh
  via `scripts/refresh_news.py`, offline, and commit the CSV.

## Running it locally

    python3 -m venv venv && source venv/bin/activate
    pip install -r requirements.txt
    python app.py                      # http://127.0.0.1:5051

Production runs via `Procfile` (`gunicorn app:app --timeout 60`), deployed to
what looks like Render (`app.py`'s host/port fallback comments name it
explicitly).

## Live draft tracker ("Run your draft")

Everything else on the page is **pre-draft** planning. The live tracker
layered on top of it (state lives entirely in `static/app.js`, no new
routes — see `static/AGENTS.md`) is what actually helps during the real
draft: log every real pick, in turn order, as it happens (yours and
everyone else's), and get a "draft queue" that re-ranks itself as the room
moves, a floor/ceiling comparison chart, and a roster panel that always
shows a full team — your real picks plus a best-guess prediction for every
slot you haven't filled yet, each clearly labeled "Locked in" or
"Predicted" so the two are never confused.

Once tracking starts, `cfg()` changes what it sends on EVERY subsequent
request (not just tracker-specific ones): `order` gets your real picks
moved to the front (so the server's own mock-draft mechanic for "my" team
takes them immediately) followed by the rest of the board with every gone
player removed, and a new `gone_players` list rides along so the server can
drop those same names from its pool. This is why "Simulate the season" and
the pre-draft board's availability check both automatically reflect a
live draft in progress without needing separate tracker-aware endpoints —
see the `_exclude_gone` note in "Request flow" above.

Two things this does NOT do, worth knowing before extending it:
- It doesn't track full opponent rosters (only *your* roster and the set of
  gone players) — no opponent-need modeling, only your own.
- The queue's "run detection" is a simple recent-picks heuristic in
  `static/app.js` (`recentPositionShare`), not a simulation — it's separate
  from `engine.sim.check_availability`'s Monte Carlo math, which IS now
  reachable mid-draft (see above) but isn't wired into the queue itself.

See `static/AGENTS.md` for exactly how the queue scoring, roster fill, and
chart work.
