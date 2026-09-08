# engine/ — the ranking, scoring and simulation pipeline

This package is the entire product. `app.py` is a thin HTTP wrapper around
these modules — if you're changing *what the tool recommends*, you're
changing something in here.

## Data flow, in dependency order

1. **scoring.py** — league-agnostic point calculator. `season_points(row,
   scoring)` takes one player-shaped dict and a scoring settings dict and
   returns fantasy points. Everything downstream calls this; it has no
   knowledge of pools, rosters, or leagues, only single rows.
   `dst_points_per_game` integrates the DST tier table over a normal spread
   rather than reading off the season-average tier, so 16.5 and 18.0 ppg
   defenses don't collapse into the same bucket.
2. **gamelogs.py** — fetches real ESPN weekly box scores (roster index
   cached 12h, gamelogs cached 7d in `data/*.json`) and reduces them to
   `weekly_stats()` (mean/sd/floor/ceiling under a league's scoring). This is
   descriptive, not predictive — it says how variable a player HAS been, not
   how variable he will be.
3. **blend.py** — `load_pool()` is the main entry point everything else
   calls. Merges `data/projections.csv` rows with `gamelogs` actuals at a
   user-set weight (`last_weight`, 0-100), applies `BACKFILL_DISCOUNT` to
   ESPN-actuals-derived rows (`src=espn*`, added via addplayer/backfill_depth)
   so a career year doesn't outrank a considered projection, and computes
   `skill_compression()` — a single ratio describing how much this league's
   scoring shrinks skill-position points relative to the site's half-PPR
   default, used by `rank.py` to decide whether K/DEF need to move earlier
   than their default placement. It also attaches `weekly_avg`/`floor`/
   `ceiling` (from `gamelogs.weekly_stats`, when a player has enough
   measured games) — these three are on a **per-week** scale and must
   never be plotted or compared against `pts`, which is a **season total**
   that also blends in next year's projection. The frontend's comparison
   chart (`static/AGENTS.md`) depends on this separation; keep it if you
   touch either field.
4. **vorp.py** — `add_vorp()` attaches `vorp`/`posRank`/`dropToNext` to every
   player in the pool, mutating in place. Replacement rank is DERIVED from
   league size and roster shape (`replacement_ranks()`), never hardcoded —
   this is the thing that makes the tool generalize across league sizes.
   `FLEX_SPLIT` distributes flex slots across eligible positions by how often
   each is actually taken there (a WR/RB/TE flex is disproportionately a
   WR). `STREAM_DISCOUNT` shrinks K/DEF's VORP since you don't need to
   roster a *good* one, just a streamable one.
5. **rank.py** — `build_list()` calls `add_vorp` then places K/DEF at a
   fixed round-fraction (`TARGET_ROUND_FRAC`) rather than by raw value,
   because a two-deep K/DEF list reliably loses both before a real draft
   reaches you (measured: 18/36 test drafts with a 2-deep list finished
   kicker-less, 1/36 at 4-deep). `compression` (from `blend.skill_compression`)
   pulls that placement earlier in leagues where skill scoring is depressed
   enough that K/DEF's relative value actually shifted.
6. **sim.py** — the biggest module. `_draft()` runs one full snake draft:
   your team follows your own list (identical mechanic to Yahoo's real
   autodraft — the whole tool exists to hand it a better list), opponents
   split into `LiveManager` (reach/run-chase/need-weighted behavior off a
   small candidate window) and mechanical autodraft (walks a fixed board
   top-to-bottom). `LATE_POSITIONS`/`FILL_REQUIRED_AFTER` are hard gates
   against K/DEF, not soft penalties — earlier soft-scoring approaches
   measurably failed (a kicker landing at median opponent pick 69.5 against
   a real-world floor near 190). Three public entry points sit on top of
   `_draft`:
   - `likely_roster()` — one representative full draft outcome (starters +
     bench), for the pre-sim roster preview.
   - `check_availability()` — for each of your next ~40 targets, odds he's
     still there at YOUR actual next relevant pick, run via Monte Carlo
     against the default board. Read the docstring on `target_pick`
     carefully before touching it — getting "which of my picks is the real
     decision point" wrong for an off-cadence draft slot is a well-
     documented easy mistake (see the two failure modes in the docstring).
   - `run_raw()`/`summarize()`/`run()` — the season simulation (injuries,
     hot/cold streaks, waiver adds), batchable so the streaming
     `/api/simulate` endpoint can animate real progress.
7. **news.py** — reads `data/player_news.csv` only, never the network.
   `attach()` must never raise or block — news is an enhancement, not a
   dependency (a prior live-fetch version 500'd production).
8. **addplayer.py** — search + add a single player missing from the pool.
   `build_row()` pro-rates his 2025 ESPN game log to a full season (capped
   at `MAX_SCALE=1.35x` so a 3-game sample doesn't get wildly extrapolated)
   and is the ONLY thing allowed to call `append_to_shared_pool()` —
   position/team confirmation against ESPN's own roster IS the trust gate,
   not a human review step.
9. **draft_tips.py** — a hand-collected, dated (2026-08-30) snapshot of real
   sourced draft-strategy claims (`DRAFT_TIPS`), matched by name against the
   pool. Not player news — this is deliberately separate from `news.py`
   (strategy content vs. real-time transactions/injuries). Goes stale like
   any hand-built reference; re-collect before each new season.

## Conventions

- Every public function takes league shape (`teams`, `roster`, `bench`,
  `scoring`) as explicit arguments rather than reading global config — there
  is no global config to read (see the root AGENTS.md's stateless-server
  note).
- Constants that encode a measured/tested threshold carry a comment with the
  measurement, not just the value (grep `measured` across this package).
  Preserve that when editing — a future change to one of these needs the
  same kind of evidence, not a guess.
- `pool` dicts are mutated in place by `add_vorp`/`attach` rather than
  copied — most of this pipeline runs once per request on a ~200-300 row
  list, so this is a deliberate simplicity/perf tradeoff, not an oversight.

## If you build the live-draft-assistant feature (see root AGENTS.md)

The pieces to reuse, in order of relevance: `vorp.replacement_ranks`
(recompute against remaining open slots), `sim.check_availability`'s
target-pick logic (conditioned on real gone-players instead of a simulated
board), `rank.build_list`'s K/DEF placement (still needed until K/DEF are
actually drafted). Avoid a second, parallel ranking implementation — the
`sim.py` docstring already documents one real bug (the opponent
kicker-sniping defect) that happened specifically because
`check_availability` once had its own separate draft loop instead of calling
`_draft()`.
