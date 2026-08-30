"""Draft the league, in real snake order, then play the season. Many times.

Your team drafts from YOUR list. The field of opponents is split into two
different kinds of drafter, because a real 14-team Yahoo league is not 13
identical robots:

  * LIVE drafters (roughly half the field): a human at the keyboard, working
    mostly off ADP with real tendencies — reaching for a favorite a round
    early, piling onto a positional run once one starts, refusing to touch a
    kicker or defense until deep in the draft no matter how the board reads.
    Ported from the original single-league version of this tool
    (engine.draft.Manager in the sibling rankmydraft project), which modeled
    this directly rather than approximating it with noise on a static board.

  * AUTODRAFT opponents (the other half): nobody at the keyboard. Yahoo's
    autopick mechanically walks a DEFAULT prerank (ADP order, kicker and
    defense pushed to a realistic round) top to bottom, filling each
    starting slot exactly once and skipping anyone whose slot is already
    full, then does the same for the bench. No judgment, no reaching, no
    reacting to a run — just the literal rule this whole tool exists to
    exploit, run against everyone else's team too.

This replaces an earlier version that ran a single "sort the whole field by
a jittered ADP number" mechanic for every opponent, live and auto alike. It
produced a real, measured defect: a kicker with real-world ADP 105 was
landing at a median opponent pick of 69.5 across 60 trials, because ADP
labels borrowed from a much larger real player pool collapse to a far
earlier true rank in this smaller one, and nothing in that mechanic
distinguished "a human would never do this" from "the math says he might."
The gate below (LATE_POSITIONS, an outright refusal rather than a soft
placement heuristic) and the mechanical autodraft walk (which places K/DEF
by ROUND, never by a jittered value) both exist specifically to make that
class of error impossible to reintroduce.

Each simulated season adds what season totals hide:

  * weekly scores drawn from each player's measured week-to-week spread
  * injuries (a player misses a stretch, sometimes the rest of the year)
  * breakouts and busts (a few players run 50% hot or cold all season)
  * waiver moves: when a lineup slot cannot be filled, the team signs a
    replacement-level player at that position and drops its worst spare.
    Every team does this, at the same flat value, so no team gets first
    claim on a shared pool just because its _lineup() call happens first
    in the loop that week.

The fairness check that keeps this honest: hand the simulator Yahoo's own
default prerank as "your" list and average across every draft slot — it
must win about half its games. Snake drafts do not treat every slot
equally (a team at slot 1 gets picks 1 and 24, a 23-pick gap; slot 6 gets
6, 19, 30, 43..., an even ~11-13 pick gap every round, never a
back-to-back pair) so the average hides real per-slot variance, and it
should. The meaningful comparison is never "does this slot land on exactly
7.0" but "does a custom list beat the default list AT THE SAME SLOT."
"""
from __future__ import annotations

import random
import statistics

REGULAR_WEEKS = 14

# Chance a player suffers an injury at some point in a season, by position,
# roughly in line with published missed-game rates. When it happens he misses
# 1-5 weeks; about one injury in eight ends the season.
INJURY_CHANCE = {"QB": 0.12, "RB": 0.25, "WR": 0.18, "TE": 0.15,
                 "K": 0.04, "DEF": 0.0}
SEASON_ENDER = 0.12

# Chance a player runs hot (x1.5) or cold (x0.5) for the whole season.
STAR_CHANCE = 0.07
DUD_CHANCE = 0.07

MIN_WEEK = {"QB": -3.0, "DEF": -5.0}

# ── live opponents: humans, roughly ──────────────────────────────────────
#
# Half the field (see split_live_auto below). Each gets a persistent
# temperament for the whole draft — the league-mate who always reaches is
# the same person every round — and picks off a small candidate window
# around the front of the ADP-scattered board rather than a single "next
# name up," so need, a run, and a reach all have something to compete over.
#
# "Everyone takes the best player left" is what makes a simulated draft
# smoother than a real one. Real drafts clump for reasons that are well
# documented and easy to model directly instead of approximating with noise:
#
#   1. Positional runs. Two running backs go and the room panics; the next
#      few picks skew heavily to that position regardless of value.
#   2. Roster needs. A manager with two quarterbacks does not take a third,
#      no matter where the board says he ranks.
#   3. Reaching. Managers take their guy a round early rather than risk it.
#   4. Nobody drafts a kicker in round 4. LATE_POSITIONS below is an outright
#      gate, not a soft preference — this is the rule the entire earlier
#      "board rank" mechanic existed to approximate and got measurably wrong
#      (a kicker landing at a median pick of 69.5). A live drafter simply
#      will not take one that early; there is no partial credit for "the
#      board briefly suggested it."

LIVE_ADP_NOISE_BASE = 6.0
LIVE_ADP_NOISE_GROWTH = 0.10
RUN_WINDOW = 6           # picks that count as "recent" for detecting a run
RUN_STRENGTH = 0.55      # how strongly a run pulls the next pick to that position
REACH_CHANCE = 0.22      # how often a manager takes someone earlier than value
REACH_DEPTH = 8          # how far down the board a reach can reach
CANDIDATE_WINDOW = REACH_DEPTH * 2   # how many names a live drafter even considers

# Fraction of the draft that must elapse before K/DEF are even candidates
# for a live drafter, full stop — not a penalty, a gate.
LATE_POSITIONS = {"K": 0.80, "DEF": 0.72}

# Fraction of the draft after which a live drafter still missing a mandatory
# starter (most often K or DEF, since LATE_POSITIONS holds them back) goes
# and fills it rather than keep taking best-available. Without this, a
# manager who never lucked into a kicker inside his usual candidate window
# finishes the whole draft without one. Matches LATE_POSITIONS["DEF"] — the
# earliest gated position becomes fair game — rather than sitting near the
# very end of the whole draft, which left most teams' OWN picks already
# exhausted before the force-fill could ever trigger for them (measured:
# 45% of opponent rosters illegal at 0.90; 0% at 0.72).
FILL_REQUIRED_AFTER = 0.72


def _missing_required(have: dict, roster_shape: dict) -> set:
    """Dedicated starting slots this roster still cannot fill."""
    return {pos for pos, need in roster_shape.items()
            if "/" not in pos and have.get(pos, 0) < need}


class LiveManager:
    """One live opponent, with habits that persist across the whole draft."""

    __slots__ = ("reach", "need_weight", "run_chase", "roster", "players")

    def __init__(self, rng: random.Random):
        # Spread of temperaments: some managers are disciplined, some are not.
        self.reach = rng.uniform(0.5, 1.6) * REACH_CHANCE
        self.need_weight = rng.uniform(0.4, 1.5)
        self.run_chase = rng.uniform(0.3, 1.5)
        self.roster: dict[str, int] = {}
        self.players: list[dict] = []

    def wants(self, pos: str, roster_shape: dict) -> float:
        """How much this manager still needs the position, 0-1ish."""
        have = self.roster.get(pos, 0)
        want = roster_shape.get(pos, 0)
        for slot, n in roster_shape.items():
            if "/" in slot and pos in slot.upper().split("/"):
                want += n * 0.5
        if want <= 0:
            return 0.15                      # bench flier
        return max(0.1, 1.0 - have / max(want, 0.5))


def pick_numbers(slot: int, teams: int, rounds: int,
                 style: str = "snake") -> list[int]:
    """Overall pick numbers belonging to one draft slot."""
    linear = (style or "snake").lower().startswith("lin")
    picks = []
    for rnd in range(rounds):
        if linear or rnd % 2 == 0:
            picks.append(rnd * teams + slot)
        else:
            picks.append(rnd * teams + (teams - slot + 1))
    return picks


def _slot_for(pos: str, roster: dict, filled: dict) -> str | None:
    if roster.get(pos, 0) > filled.get(pos, 0):
        return pos
    for slot, count in roster.items():
        if "/" in slot and pos in slot.upper().split("/") \
                and count > filled.get(slot, 0):
            return slot
    return None


def _board_noise(rank: float, scale: bool) -> float:
    """Standard deviation of one player's scatter off his BOARD RANK (his
    position once _default_board below has placed him, not his raw ADP
    number).

    Flat noise is wrong here: a fixed +/-5 is nothing at rank 150, where
    consensus is thin anyway, but it is enormous at rank 1-5, where real
    drafts are extremely locked in (the gap between the #1 and #6 overall
    picks is only 5 spots). Scaling with RANK means the very top of the
    board stays close to a lock while the scatter that makes the
    mid-to-late board interesting is preserved.
    """
    if not scale:
        return 0.0
    return AVAILABILITY_NOISE_BASE + AVAILABILITY_NOISE_GROWTH * rank


# How opponents place K/DEF, mirrored from engine.rank.build_list: fixed
# fraction of the way through the draft, not sorted by raw ADP value.
#
# The bug this exists to fix: this pool is ~186 players deep, and a K's ADP
# label (e.g. 105) is real-world data borrowed from drafts with hundreds of
# skill players below it. In THIS pool only ~68 skill players have a lower
# ADP, so sorting straight by ADP value collapses "ADP 105" down to true
# rank 70 — round 5 of a 14-team draft. No real drafter, autopick included,
# takes a kicker in round 5. Measured: with a raw ADP sort, opponents drafted
# a kicker at a median of pick 69.5 across 60 trials, against a real-world
# floor around pick 190+. Round-fraction placement is what keeps this from
# happening to the USER's own list (engine.rank.TARGET_ROUND_FRAC); the
# opponent board needs the identical protection, since it is standing in for
# "what would autodraft do with a normal list," and a normal list does not
# rank a kicker 70th either.
_BOARD_ROUND_FRAC = {"K": 0.80, "DEF": 0.87}


def _default_board(pool: list[dict], teams: int, rounds: int) -> list[dict]:
    """Zero-noise draft order opponents scatter around: ADP order for skill
    positions, K/DEF pushed to a fixed fraction of the draft regardless of
    their raw ADP value. Every K/DEF in the pool is kept (not just a
    handful) — up to `teams` opponents each need one, so trimming the way
    engine.rank.build_list does for a single team's list would starve the
    field."""
    skill = [p for p in pool if p["pos"] not in _BOARD_ROUND_FRAC]
    skill.sort(key=lambda p: p.get("adp") or 999)
    out = list(skill)

    for pos, frac in _BOARD_ROUND_FRAC.items():
        group = sorted([p for p in pool if p["pos"] == pos],
                       key=lambda p: p.get("adp") or 999)
        if not group:
            continue
        target = min(int(rounds * frac * teams), len(out))
        for i, p in enumerate(group):
            out.insert(min(target + i, len(out)), p)

    return out


def split_live_auto(teams: int, my_slot: int, rng: random.Random) -> set[int]:
    """Which opponent slots (1-indexed, my_slot excluded) draft live this
    trial. Roughly half, drawn fresh per trial so a season's worth of
    simulated drafts sees a mix rather than the same 6 seats always being
    the "engaged" managers."""
    others = [s for s in range(1, teams + 1) if s != my_slot]
    rng.shuffle(others)
    return set(others[: len(others) // 2])


def _autodraft_pick(order: list[dict], roster: dict, filled: dict,
                    starters: int, have: int, gone: set,
                    wide_pool: list[dict] | None = None) -> dict | None:
    """One autodraft opponent's pick: walk its OWN prerank top to bottom —
    starters first, skip full slots, fall through to bench once starters
    are done. This is Yahoo's actual, literal autopick rule; nothing here
    reaches, chases a run, or reacts to anything.

    wide_pool: the full player pool, searched only when `order` itself has
    run dry on someone who fits a still-open slot. A user's own list keeps
    just a handful of kickers and defenses (engine.rank.KEEP); if every one
    of them gets drafted by someone else before this team's own turn for
    that slot comes up, `order` alone has nowhere left to look, and without
    this the pick would fall through to "best available, any position" and
    silently double up a skill slot instead — the exact defect a prior
    session found and fixed (measured at 28% of drafts under plain standard
    scoring before the fix existed).
    """
    chosen = None
    if have < starters:
        for p in order:
            if p["name"] in gone:
                continue
            s = _slot_for(p["pos"], roster, filled)
            if s:
                chosen = p
                filled[s] = filled.get(s, 0) + 1
                break
        if chosen is None and wide_pool is not None:
            for p in wide_pool:
                if p["name"] in gone:
                    continue
                s = _slot_for(p["pos"], roster, filled)
                if s:
                    chosen = p
                    filled[s] = filled.get(s, 0) + 1
                    break
    if chosen is None:
        for p in order:
            if p["name"] not in gone:
                chosen = p
                break
    return chosen


def _live_pick(default_order: list[dict], default_rank: dict[str, int],
              roster: dict, mgr: LiveManager, progress: float,
              run_recent: list[str], gone: set, rng: random.Random) -> dict | None:
    """One live opponent's pick: a small candidate window off the front of
    the (rank-scattered) board, scored by roster need, a positional run, a
    hard gate on K/DEF until late, and a chance to reach for a favorite.
    Ported from engine.draft.Manager in the sibling rankmydraft project.
    """
    pool_c: list[dict] = []
    for p in default_order:
        if p["name"] in gone:
            continue
        pool_c.append(p)
        if len(pool_c) >= CANDIDATE_WINDOW:
            break
    if not pool_c:
        return None

    # Late in the draft, a manager still missing a mandatory starter (almost
    # always K or DEF, since the gate below holds them back) goes and gets
    # one, searching the WHOLE remaining board rather than just the
    # candidate window — a kicker sitting 100 picks down the ADP order is
    # never a candidate otherwise, and every live opponent would finish the
    # draft without one, forfeiting the slot all season.
    if progress >= FILL_REQUIRED_AFTER:
        need_pos = _missing_required(mgr.roster, roster)
        if need_pos:
            forced = next((c for c in default_order
                          if c["pos"] in need_pos and c["name"] not in gone),
                         None)
            if forced:
                pool_c = [forced]

    best_p, best_score = pool_c[0], -1e9
    for rank_i, cand in enumerate(pool_c):
        pos = cand["pos"]
        score = -rank_i * 1.0              # board order is the baseline

        score += mgr.wants(pos, roster) * 3.0 * mgr.need_weight

        recent = run_recent[-RUN_WINDOW:]
        if recent:
            share = recent.count(pos) / len(recent)
            score += share * RUN_STRENGTH * 6.0 * mgr.run_chase

        # The gate: an outright refusal, not a penalty a big enough score
        # can outweigh. A live drafter simply does not have a kicker or
        # defense in his candidate window this early, full stop.
        gate = LATE_POSITIONS.get(pos)
        if gate is not None and progress < gate:
            continue

        if rng.random() < mgr.reach:
            score += rng.uniform(0, 4.0)

        if score > best_score:
            best_score, best_p = score, cand

    return best_p


def _draft(order: list[dict], pool: list[dict], slot: int, teams: int,
           roster: dict, bench: int, style: str,
           rng: random.Random,
           scatter: bool = False,
           track_picks: bool = False
           ) -> tuple[list[dict], list[list[dict]], dict[str, int] | None]:
    """One draft, in real snake order. Returns (my roster, opponent rosters,
    taken_at). taken_at maps player name -> the overall pick number he was
    drafted at, but is only populated when track_picks=True — building it
    costs nothing extra in the loop, but callers that do not need per-pick
    timing (the season simulator, run many times per trial) should not pay
    for a bigger dict than they use.

    scatter=False (the season simulation's setting): every opponent, live or
    auto, follows their respective mechanic with ZERO extra jitter beyond
    what each mechanic already models on its own (a live drafter's reach
    chance, a positional run) — that is the fairness premise the null test
    checks. scatter=True (the pre-draft tools) additionally jitters the
    shared ADP board those mechanics read from, since "will he survive to my
    pick" against a perfectly static board is either 0% or 100% every time.
    """
    starters = sum(roster.values())
    rounds = starters + bench
    default_order = _default_board(pool, teams, rounds)
    default_rank = {p["name"]: i for i, p in enumerate(default_order)}
    if scatter:
        scattered = sorted(
            pool, key=lambda p: default_rank.get(p["name"], 999)
            + rng.gauss(0, _board_noise(default_rank.get(p["name"], 999), True)))
    else:
        scattered = default_order

    live_slots = split_live_auto(teams, slot, rng)
    managers = {s: LiveManager(rng) for s in live_slots}
    run_recent: list[str] = []

    linear = (style or "snake").lower().startswith("lin")
    gone: set[str] = set()
    mine: list[dict] = []
    my_filled: dict[str, int] = {}
    opp: list[list[dict]] = [[] for _ in range(teams)]
    opp_filled: list[dict] = [{} for _ in range(teams)]
    total_picks = teams * rounds
    taken_at: dict[str, int] | None = {} if track_picks else None

    for pick in range(1, total_picks + 1):
        rnd, i = divmod(pick - 1, teams)
        owner = i + 1 if (linear or rnd % 2 == 0) else teams - i
        progress = pick / total_picks

        if owner == slot:
            # My own team: walk MY list, same mechanic as autodraft, since
            # that is literally what Yahoo does with whatever prerank you
            # hand it — the entire reason this tool exists is to hand it a
            # BETTER one.
            chosen = _autodraft_pick(order, roster, my_filled, starters,
                                     len(mine), gone, wide_pool=default_order)
            dest = mine
        elif owner in managers:
            chosen = _live_pick(scattered, default_rank, roster,
                                managers[owner], progress, run_recent,
                                gone, rng)
            dest = opp[owner - 1]
        else:
            chosen = _autodraft_pick(scattered, roster,
                                     opp_filled[owner - 1], starters,
                                     len(opp[owner - 1]), gone)
            dest = opp[owner - 1]

        if chosen is None:                     # board exhausted this deep
            for p in scattered:
                if p["name"] not in gone:
                    chosen = p
                    break
        if chosen is None:
            continue

        gone.add(chosen["name"])
        dest.append(chosen)
        if taken_at is not None:
            taken_at[chosen["name"]] = pick
        run_recent.append(chosen["pos"])
        if owner in managers:
            mgr = managers[owner]
            mgr.roster[chosen["pos"]] = mgr.roster.get(chosen["pos"], 0) + 1
            mgr.players.append(chosen)

    return mine, [r for r in opp if r], taken_at


def _lineup(team: list[dict], roster: dict, week: int, out: dict,
            mult: dict, rng: random.Random,
            fa: dict, drops_allowed: bool) -> tuple[float, int]:
    """Score one team's week. Signs a free agent if a slot would sit empty.

    Returns (points, waiver adds made)."""
    adds = 0

    def available(t):
        return [p for p in t
                if p["bye"] != week and week not in out.get(p["name"], ())]

    # A required slot with no eligible body on the roster gets a waiver add,
    # at replacement level for that position. Every team's waiver pickups are
    # drawn from the SAME replacement-level value, independent of which team
    # is processed first in a given week — pulling from a shared list of real
    # undrafted players instead would give whichever team's _lineup() call
    # happens to run first in the loop first claim on the best one, every
    # week, all season. That bias would compound across 14 weeks in favor of
    # whichever team the caller always lists first, which was "mine."
    if drops_allowed:
        for pos, need in roster.items():
            if "/" in pos:
                continue
            have = sum(1 for p in available(team) if p["pos"] == pos)
            guard = 0
            while have < need and guard < 3:
                guard += 1
                new = dict(fa["_level"][pos])
                # Drop the worst player from a position with bodies to spare,
                # never the only one filling a required slot.
                counts: dict[str, int] = {}
                for p in team:
                    counts[p["pos"]] = counts.get(p["pos"], 0) + 1
                spare = min((p for p in team
                             if counts[p["pos"]] > roster.get(p["pos"], 0)),
                            default=None, key=lambda p: p["pts"])
                if spare:
                    team.remove(spare)
                team.append(new)
                adds += 1
                have = sum(1 for p in available(team) if p["pos"] == pos)

    drawn = []
    for p in available(team):
        v = rng.gauss(p["pts"] / 17.0 * mult.get(p["name"], 1.0), p["sd"])
        drawn.append((max(v, MIN_WEEK.get(p["pos"], 0.0)), p))
    drawn.sort(key=lambda t: -t[0])

    used: set[int] = set()
    filled: dict[str, int] = {}
    total = 0.0
    for flex_pass in (False, True):
        for s, count in roster.items():
            if ("/" in s) != flex_pass:
                continue
            allowed = set(s.upper().split("/")) if flex_pass else {s}
            for i, (v, p) in enumerate(drawn):
                if filled.get(s, 0) >= count:
                    break
                if i in used or p["pos"] not in allowed:
                    continue
                used.add(i)
                filled[s] = filled.get(s, 0) + 1
                total += v
    return total, adds


def run_raw(order: list[dict], pool: list[dict], slot: int, teams: int,
           roster: dict, bench: int, style: str = "snake",
           trials: int = 150, seed: int = 7) -> dict:
    """Seasons for one list, as raw per-trial lists rather than an aggregate.

    Split out from run() so a caller (the streaming /api/simulate endpoint)
    can run this in small batches with different seeds and merge the raw
    lists across batches, animating real progress rather than faking a
    progress bar around one instant computation — 200 trials of this
    actually takes under half a second, which reads as broken, not fast,
    when the button says "simulating."
    """
    rng = random.Random(seed)
    wins_all: list[int] = []
    pts_all: list[float] = []
    injured_starts = 0
    my_adds = 0

    for t in range(trials):
        mine, opps, _ = _draft(order, pool, slot, teams, roster, bench,
                               style, rng)
        if not opps:
            continue

        # Waiver adds are drawn at a flat replacement-level value per
        # position (see the comment in _lineup for why: pulling from a
        # shared list of actual undrafted players would give whichever
        # team's turn came first in the loop a persistent, order-dependent
        # advantage across the season).
        fa: dict = {"_level": {}}
        for pos in {p["pos"] for p in pool}:
            worst = min((p["pts"] for p in pool if p["pos"] == pos),
                        default=60.0)
            fa["_level"][pos] = {"name": f"Waiver {pos}", "pos": pos,
                                 "team": "", "bye": 0,
                                 "pts": round(worst * 0.8, 1),
                                 "sd": 5.0}

        # Season-long fates.
        out: dict[str, set] = {}
        mult: dict[str, float] = {}
        for p in pool:
            r = rng.random()
            if r < STAR_CHANCE:
                mult[p["name"]] = 1.5
            elif r < STAR_CHANCE + DUD_CHANCE:
                mult[p["name"]] = 0.5
            if rng.random() < INJURY_CHANCE.get(p["pos"], 0.1):
                start = rng.randint(1, REGULAR_WEEKS)
                dur = 99 if rng.random() < SEASON_ENDER else rng.randint(1, 5)
                out[p["name"]] = set(range(start, min(start + dur,
                                                      REGULAR_WEEKS + 1)))

        my_team = list(mine)
        opp_teams = [list(r) for r in opps]
        rotation = list(range(len(opp_teams)))
        rng.shuffle(rotation)

        wins = 0
        season_pts = 0.0
        for week in range(1, REGULAR_WEEKS + 1):
            injured_starts += sum(1 for p in my_team
                                  if week in out.get(p["name"], ()))
            me, adds = _lineup(my_team, roster, week, out, mult, rng,
                               fa, True)
            my_adds += adds
            them, _ = _lineup(opp_teams[rotation[(week - 1) % len(rotation)]],
                              roster, week, out, mult, rng, fa, True)
            season_pts += me
            if me > them:
                wins += 1
        wins_all.append(wins)
        pts_all.append(season_pts)

    return {
        "wins": wins_all, "points": pts_all,
        "injuredStarts": injured_starts, "waiverAdds": my_adds,
    }


def summarize(raw: dict) -> dict:
    """Turn accumulated run_raw() output into the same dict run() used to
    return directly. Callable on a partial accumulation too, so a streaming
    endpoint can summarize-so-far after every batch."""
    wins_all = sorted(raw["wins"])
    n = len(wins_all)
    if not n:
        return {}
    return {
        "trials": n,
        "weeks": REGULAR_WEEKS,
        "meanWins": round(statistics.mean(wins_all), 1),
        "lowWins": wins_all[n // 10],
        "highWins": wins_all[(9 * n) // 10],
        "dist": {w: wins_all.count(w) for w in sorted(set(wins_all))},
        "meanPoints": round(statistics.mean(raw["points"])),
        "injuredStartsPerSeason": round(raw["injuredStarts"] / n, 1),
        "waiverAddsPerSeason": round(raw["waiverAdds"] / n, 1),
    }


def run(order: list[dict], pool: list[dict], slot: int, teams: int,
        roster: dict, bench: int, style: str = "snake",
        trials: int = 150, seed: int = 7) -> dict:
    """One-shot convenience wrapper around run_raw() + summarize(), for
    callers (tests, other tools) that just want a final result."""
    raw = run_raw(order, pool, slot, teams, roster, bench, style,
                  trials, seed)
    return summarize(raw)


# Scatter used only for the pre-draft tools below (likely_roster,
# check_availability), never the season simulation. See the note on _draft:
# zero scatter makes every trial identical, which cannot express odds or
# show a range of plausible rosters.
#
# Grows with ADP rather than a flat value: tight at the top of the board,
# looser by the late rounds where consensus is thin. Matches the values the
# prior version of this tool shipped with and validated (Gibbs at ADP 1
# showing ~24% available at pick 6 — that looks aggressive until you check
# it against the old tool's numbers, which land in the same place; different
# managers really do disagree that much about the very top of a draft).
AVAILABILITY_NOISE_BASE = 6.0
AVAILABILITY_NOISE_GROWTH = 0.10
AVAILABILITY_TRACK = 40    # how far down your own list to check at all


def likely_roster(order: list[dict], pool: list[dict], slot: int, teams: int,
                  roster: dict, bench: int, style: str = "snake",
                  trials: int = 25, seed: int = 3) -> list[dict]:
    """A representative full roster your list tends to produce (starters AND
    bench), drafted against Yahoo's default board. Fast (no season logic) —
    meant to run before the season simulation so a user sees what they're
    about to test, bench included, not just who starts.

    Runs several quick drafts and returns the single one closest to the
    MEDIAN starter points, rather than combining the most-frequent player
    per slot across drafts — that alternative produces a roster nobody's
    draft actually assembled (e.g. three real running backs plus a
    flex-eligible fourth all landing on the "team" at once, because each was
    independently a frequent RB across different trials). The full drafted
    roster (not just the starters slice) is what gets returned, so the bench
    shown is the ACTUAL bench from that one representative draft.
    """
    rng = random.Random(seed)
    starters = sum(roster.values())
    drafts = []
    for _ in range(trials):
        mine, _, _ = _draft(order, pool, slot, teams, roster, bench, style,
                            rng, scatter=True)
        drafts.append(mine)

    totals = sorted(range(len(drafts)),
                    key=lambda i: sum(p["pts"] for p in drafts[i][:starters]))
    median_draft = drafts[totals[len(totals) // 2]]

    filled: dict[str, int] = {}
    result = []
    for i, p in enumerate(median_draft):
        s = _slot_for(p["pos"], roster, filled) if i < starters else None
        if s:
            filled[s] = filled.get(s, 0) + 1
        else:
            s = "BENCH"
        result.append({**{k: p.get(k) for k in
                          ("name", "pos", "team", "bye", "pts")},
                      "slot": s})
    return result


def check_availability(order: list[dict], pool: list[dict], slot: int,
                       teams: int, roster: dict, bench: int,
                       style: str = "snake", trials: int = 300,
                       seed: int = 11) -> list[dict]:
    """For each of your next ~40 targets, how often he lasts to your pick.

    Reordering your list changes who you compete with for a spot and what
    you already have when a pick comes up, but it does NOT change when
    opponents pick or what they draft (they always follow Yahoo's default
    board) — so this answers "can I get this guy where my list has him"
    rather than "how will the whole draft unfold."
    """
    rng = random.Random(seed)
    starters = sum(roster.values())
    rounds = starters + bench
    my_picks = sorted(pick_numbers(slot, teams, rounds, style))

    watch = order[:AVAILABILITY_TRACK]
    survived: dict[str, int] = {p["name"]: 0 for p in watch}

    # Board rank, not raw ADP, for the same reason _draft() uses it: a K/DEF's
    # ADP number is real-world data that does not describe where he actually
    # sits in THIS pool (see _default_board's docstring — ADP 105 collapsing
    # to true rank 70 in a 186-player pool is not a corner case, it happens
    # to every kicker and defense here).
    default_order = _default_board(pool, teams, rounds)
    board_rank = {p["name"]: i for i, p in enumerate(default_order)}

    # Which of MY picks is the real decision point for each player.
    #
    # Two ways to get this wrong, both by only looking at picks AFTER his
    # board rank:
    #   1. A player ranked 3rd, for someone drafting slot 1: the first of my
    #      picks at or after 3 is pick 24, which comes back 0% available —
    #      true and useless, since the real decision was pick 1, where he is
    #      a lock.
    #   2. A player ranked 21st, when my picks are [6, 19, 30, ...]: pick 19
    #      is BEFORE his rank and well within reach, but "first of my picks
    #      >= rank" skips straight past it to pick 30 — by which point he is
    #      almost always long gone — and reports him as 100% available at a
    #      pick where he never actually shows up.
    #
    # The fix is the same for both: take the EARLIEST of my picks that falls
    # within his plausible range (rank +/- 2 standard deviations), and only
    # fall back to "my pick right before his window opens" if none of my
    # picks land inside it at all.
    target_pick: dict[str, int] = {}
    for p in watch:
        r = board_rank.get(p["name"], 999)
        spread = 2.0 * _board_noise(r, True)
        lo, hi = r - spread, r + spread
        in_range = [k for k in my_picks if lo <= k <= hi]
        if in_range:
            target_pick[p["name"]] = in_range[0]
        else:
            earlier = [k for k in my_picks if k < lo]
            later = [k for k in my_picks if k > hi]
            target_pick[p["name"]] = (earlier[-1] if earlier
                                      else later[0] if later
                                      else my_picks[0] if my_picks else None)

    # Runs the SAME draft mechanic _draft() uses everywhere else (live/auto
    # opponent split, the hard K/DEF gate, positional runs) instead of a
    # second, independent implementation of a draft loop. Two copies of this
    # logic is exactly how the earlier "opponents chase kickers absurdly
    # early" defect went unnoticed here after it was fixed in _draft() for
    # the season simulator — this function kept its own separate loop that
    # never got the same fix.
    for _ in range(trials):
        _, _, taken_at = _draft(order, pool, slot, teams, roster, bench,
                                style, rng, scatter=True, track_picks=True)

        # "Survived" means still on the board at MY target pick — taken by
        # ME at or after it counts (I got him when I meant to), taken by
        # anyone else BEFORE it does not (he was gone before my decision
        # point came up).
        for p in watch:
            tgt = target_pick.get(p["name"])
            if tgt is None:
                continue
            if taken_at.get(p["name"], 10**9) >= tgt:
                survived[p["name"]] += 1

    out = []
    for p in watch:
        tgt = target_pick.get(p["name"])
        if tgt is None:
            continue
        out.append({
            "name": p["name"], "pos": p["pos"], "rank": p.get("rank"),
            "adp": round(p.get("adp") or 999),
            "atPick": tgt,
            "pct": round(100 * survived[p["name"]] / trials),
        })
    return out
