# data/ — the shared player pool, news cache, and fetch caches

- **projections.csv** — the shared, authoritative player pool every
  visitor's `engine.blend.load_pool()` reads. Hand-built for the season (see
  `SOURCES.json`: "hand-built from 2025 season production, depth charts and
  preseason reporting", as of 2026-08-26) for most rows, PLUS rows appended
  at runtime:
  - by `engine.addplayer.append_to_shared_pool()` when a visitor
    searches-and-adds a real player (marked `src=espn2025`, built from a
    pro-rated 2025 game log, not a hand-built forecast),
  - by `scripts/backfill_depth.py`, same mechanism, run in bulk to widen the
    pool for deep leagues.

  Rows with `src=espn*` get `BACKFILL_DISCOUNT` (0.88x) applied downstream
  in `blend.py` since they're a measured result, not a considered
  projection. Receptions are ESTIMATED from receiving yards at
  position-typical catch rates (see `SOURCES.json`) — the set was originally
  built for a no-PPR league and never recorded real reception counts. This
  file is a live write target; check `git diff` on it after running any
  script that touches it, or after any local testing that exercises
  `/api/add-player`.
- **player_news.csv** — refreshed OFFLINE ONLY by `scripts/refresh_news.py`,
  never written by the running app. Read by `engine/news.py`, matched to
  players by full-name (not last-name — see that module's docstring for why
  last-name matching is actively wrong here) and ESPN's own athlete tags.
- **SOURCES.json** — provenance/caveats for `projections.csv`: as-of date,
  method, and the explicit caveat that these are season-long projections,
  not a live injury feed (a player hurt after `as_of` still carries his
  healthy projection).
- **gamelog_cache.json**, **roster_cache.json** — disk caches written by
  `engine/gamelogs.py` (weekly game logs, 7-day TTL; NFL roster index,
  12-hour TTL). Safe to delete; they'll be rebuilt from ESPN on next
  request. Both fall back to stale cached data rather than failing outright
  if ESPN is unreachable.

None of these files are meant to be hand-edited except `projections.csv`
(for corrections) and `SOURCES.json`. The two cache files and
`player_news.csv` are machine-generated — edit the script/module that
generates them instead of the file directly.
