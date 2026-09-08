# scripts/ — offline maintenance, never called by the running app

Both scripts here are meant to be run by a human, from a machine with
network access, on some cadence — never imported by `app.py` or anything
under `engine/`. If you find yourself wanting to call one from a request
handler, don't; that's the exact mistake `engine/news.py`'s docstring
documents fixing (a live per-request ESPN fetch 500'd production once
already).

- **refresh_news.py** — `python scripts/refresh_news.py`. Fetches all 32
  teams' recent articles from ESPN, de-dupes, overwrites
  `data/player_news.csv`. Run daily-ish during the season, then commit +
  redeploy (Render picks up the new file on push). No flags.
- **backfill_depth.py** — `python scripts/backfill_depth.py [--min-games N]
  [--target N] [--dry-run]`. One-off-but-rerunnable: widens
  `data/projections.csv` with real skill-position players not yet in the
  pool, via the exact same path `engine.addplayer.build_row()` uses for a
  single searched player (same pro-rating, same `src=espn2025` marking, same
  downstream discount). Exists because a shallow pool fails `app.py`'s
  `_pool_too_shallow` guard for deep leagues (14 teams x big bench). Only
  touches skill positions — K/DEF are already at their real-world ceiling
  (one per NFL team) and can't be deepened this way.

Both scripts write directly to `data/projections.csv` or
`data/player_news.csv` — check `git diff` on those files after running
either, before committing, since both do a full read-modify-overwrite of the
CSV.
