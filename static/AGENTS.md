# static/ — frontend: vanilla JS/CSS, no framework, no build step

`app.js` (~1080 lines) is the entire client. No React/Vue, no bundler, no
npm — a `<script>` tag loads it directly (see `templates/index.html`). Keep
it that way unless there's a real reason not to; the whole app is
deliberately "one page, no accounts, no database."

## Structure of app.js

- **`cfg()`** builds the request payload sent on essentially every POST:
  league settings read live from the DOM, plus `ORDER` (current list,
  user-editable) and `EXTRA_PLAYERS` (session-added players). This is the
  client-side half of the app's stateless-server design (root AGENTS.md) —
  the server trusts whatever `cfg()` sends every time. Once a live draft is
  being tracked (`DRAFT` non-null), `cfg()` also rewrites `order` (your real
  picks first, then the rest of the board with every gone player removed)
  and adds `gone_players`, so `/api/simulate`, `/api/availability` and
  `/api/roster-preview` all automatically plan around the real draft
  instead of the untouched pre-draft list — see `app.py`'s `_exclude_gone`.
  Any FUTURE piece of round-tripping state belongs here the same way.
- **`post(url, body)`** — thin fetch wrapper, throws on non-OK with the
  server's `error` message.
- **`applyBuild(d)`** — the one function that resets client state after
  `/api/build` returns: repopulates `ORDER`/`PLAYERS` from `d.players` (the
  curated board), then merges `d.pool` (every player, not just the board's
  top ~4 K/DEF — see "Live draft tracker" below) into `PLAYERS` for
  anything the board doesn't already have, re-renders board/tips, and
  explicitly clears the search box. Only touches the roster-preview panel
  via `renderRosterPreview(d.rosterPreview)` when NO live draft is
  running — a live draft's roster comes from `myLiveRoster()` instead (see
  below), and this must never clobber it.
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

Draft state itself is entirely client-side, same pattern as
`ORDER`/`PLAYERS` — no new server endpoint. `DRAFT = { teams, slot, style,
rounds, totalPicks, picks: [{overall, round, owner, name}] }`, built from
the league config at the moment "Start tracking this draft" is clicked.
`ownerForPick()` replicates `engine.sim._draft`'s snake-order arithmetic in
JS rather than re-deriving it — if that formula ever changes server-side,
mirror the change here too. (Existing server endpoints DO now behave
differently once this state exists — see `cfg()` above.)

- **Logging a pick** (`submitPick`) just appends to `DRAFT.picks`; whose
  turn is next is always *derived* from `picks.length`, never stored
  separately, which is what makes undo (`DRAFT.picks.pop()`) trivially
  correct — there's no second counter to desync.
- **The search box searches the FULL pool, not the board.** `ORDER`/the
  board is `engine.rank.build_list`'s curated, trimmed list — only its top
  ~4 kickers and ~4 defenses (`KEEP` in `engine/rank.py`), because a real
  draft against THAT list never goes deeper. A live draft against 11 OTHER
  real teams has no such limit — all 12+ real kickers and defenses get
  taken. The draft search and `myLiveRoster`'s predicted-fill both search
  `PLAYERS` (board + merged full pool from `applyBuild`), never bare
  `ORDER`, for exactly this reason. An earlier version searched `ORDER`
  only and would report "no one left" at K/DEF the moment the board's top 4
  were gone, with 10 more real ones still sitting in the pool untouched.
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
- **`myLiveRoster()` always returns a FULL roster**, never an empty slot
  unless the entire pool is somehow exhausted. Every starter and bench slot
  gets either your real logged pick (`actual: true`, "Locked in") or the
  best remaining player for that slot (`actual: false`, "Predicted") via
  `bestRemaining()`, which tries `ORDER` first and falls back to scanning
  all of `PLAYERS` by raw points — the same board-vs-full-pool split as the
  search box, and for the same reason (K/DEF run out of board-listed
  options fast). Predictions exclude every drafted player, any team
  (`draftedNames()`), and re-run on every pick, so they tighten up as the
  real draft actually unfolds rather than staying static. `renderRosterPreview`
  only shows the Locked-in/Predicted pill when `p.actual` is defined at
  all — the pre-draft hypothetical preview (`d.rosterPreview` from
  `/api/build`) has no such field and renders exactly as it always did.
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
