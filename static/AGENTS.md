# static/ — frontend: vanilla JS/CSS, no framework, no build step

`app.js` (580 lines) is the entire client. No React/Vue, no bundler, no
npm — a `<script>` tag loads it directly (see `templates/index.html`). Keep
it that way unless there's a real reason not to; the whole app is
deliberately "one page, no accounts, no database."

## Structure of app.js

- **`cfg()`** builds the request payload sent on essentially every POST:
  league settings read live from the DOM, plus `ORDER` (current list,
  user-editable) and `EXTRA_PLAYERS` (session-added players). This is the
  client-side half of the app's stateless-server design (root AGENTS.md) —
  the server trusts whatever `cfg()` sends every time, so any new piece of
  round-tripping state (e.g., a future live-draft-assistant's "who's already
  gone") belongs here, added to `cfg()` and read back out of the response
  the same way `ORDER`/`PLAYERS` already are.
- **`post(url, body)`** — thin fetch wrapper, throws on non-OK with the
  server's `error` message.
- **`applyBuild(d)`** — the one function that resets client state after
  `/api/build` returns: repopulates `ORDER`/`PLAYERS`, re-renders
  board/tips/roster preview, and explicitly clears the search box — a fresh
  build is a fresh ranking, nothing stale should linger.
- **`renderBoard()`** caps visible rows at `BOARD_VISIBLE_CAP=200` for
  scannability; deeper players still exist in `ORDER`/`PLAYERS` and
  participate in sim/roster-preview, they're just not rendered as rows.
- **Reordering** is both drag-and-drop (native HTML5 DnD, no library) and
  up/down arrow buttons — arrows are for precise single-spot moves, drag for
  big jumps. The up arrow additionally calls `/api/availability` and renders
  `oddsRow()` inline, showing the round he'd likely go in and survival
  odds — that's the closest thing the current UI has to draft-day decision
  support (see root AGENTS.md's gap note).
- **Settings persistence** (`SETTINGS_KEY` in `localStorage`) is
  fire-and-forget on every `input` event on `#cfg`, wrapped in try/catch
  (private browsing / full quota is not worth surfacing to the user). It
  stores league config only, never the built list or `EXTRA_PLAYERS`.
- **Sim progress** is deliberately paced client-side to a minimum
  `SIM_SECONDS=5` even though the actual computation finishes in well under
  a second — see the comment above the `$("#simgo")` handler; a number that
  just appears reads as broken for a button that says "simulating your
  season."

## Conventions

- `$`/`el` at the top of the file are the only helpers — no jQuery, no
  query-selector library.
- Copy/text comes from `COPY` (see `templates/AGENTS.md`), injected
  server-side as JSON and referenced by key (e.g. `COPY["sim-progress"]`)
  rather than hardcoded strings, so `copy.md` stays the single source of
  truth for user-facing text.

## Live draft tracker ("Run your draft")

Entirely client-side state, same pattern as `ORDER`/`PLAYERS` — no new
server endpoint. `DRAFT = { teams, slot, style, rounds, totalPicks, picks:
[{overall, round, owner, name}] }`, built from the league config at the
moment "Start tracking this draft" is clicked. `ownerForPick()` replicates
`engine.sim._draft`'s snake-order arithmetic in JS rather than re-deriving
it — if that formula ever changes server-side, mirror the change here too.

- **Logging a pick** (`submitPick`) just appends to `DRAFT.picks`; whose
  turn is next is always *derived* from `picks.length`, never stored
  separately, which is what makes undo (`DRAFT.picks.pop()`) trivially
  correct — there's no second counter to desync.
- **The queue is not the static VORP order.** `computeQueue()` re-scores
  every undrafted player as `vorp + needBoost + runBoost`:
  - `needBoost` — nonzero while a mandatory roster slot (from the league's
    own `roster` config, flex slots folded evenly across eligible
    positions) is still unfilled on *your* logged picks.
  - `runBoost` — nonzero when a position's share of the last `RUN_WINDOW`
    picks (across ALL teams, from the shared `DRAFT.picks` log) clears
    `RUN_SHARE_MIN`. This is a plain frequency heuristic, not a
    simulation — see the root AGENTS.md note on why this doesn't call
    `engine.sim`.
  Each contributing factor attaches a human-readable note (`"Fills your
  open RB slots"`, `"67% of the last 8 picks were QB — depth is thinning
  fast"`) so the recommendation is never a bare number. A player with
  neither factor firing still gets a value-only note, never a blank one.
  Don't reduce this back to a flat VORP sort — the whole point is that
  static value order is a different question than "best pick for THIS
  roster right now" (see the root AGENTS.md).
- **The comparison chart** (`renderCompareChart`) plots floor / weekly
  average / ceiling for up to `COMPARE_MAX` (4) players checked in the
  queue, built as inline SVG (no charting library, consistent with the rest
  of this file). Colors are the dataviz skill's validated dark-surface
  categorical slots 1-4, checked against this app's background with
  `scripts/validate_palette.js` before use — don't hand-pick different
  hues without re-validating. **Only plot `floor`/`weekly_avg`/`ceiling`
  together** — they come from the same per-week distribution
  (`engine.gamelogs` via `engine.blend`). Never mix in `pts` (a season
  TOTAL that also blends in next year's projection) on the same axis; an
  earlier version of this chart did exactly that and produced a
  nonsensical Floor-3/Avg-300/Ceiling-25 line before the fields were
  split apart in `engine/blend.py`.
