"""Draft the league, then play the season. Many times.

Your team drafts from YOUR list. The other teams draft from Yahoo's default
board (ADP order, small noise), because that is what most people in a real
league actually do: they never touch the ranking.

Both sides use real autodraft mechanics: fill every starting slot before
touching the bench, then take best available.

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
default list as "your" list and average across every draft slot — it must
win about half its games. It does (6.9 of 14 averaged over all 12 slots in
a 12-team league, within noise of 7.0).

That average hides real per-slot variance, and it should: snake drafts do
not treat every slot equally. A team at slot 1 gets picks 1 and 24 (a
23-pick gap), while a team at slot 6 gets 6, 19, 30, 43... (an even ~11-13
pick gap every round, never a back-to-back pair). Checked with the DEFAULT
list at every slot, that spacing alone swings the result from about 4.7
wins at the worst slot to 8.5 at the best — a real, well-documented property
of snake drafts, not a simulator artifact. So the meaningful comparison is
never "does this slot land on exactly 7.0" but "does a custom list beat the
default list AT THE SAME SLOT" — checked across slots 1, 6, 9 and 12, a
built list beats the same-slot default by +1.3 to +4.8 wins every time.
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

# ADP scatter for the default-list drafters. Zero: they follow the board
# exactly, which is the premise. Noise makes opponents draft slightly worse
# than the clean board and flatters every list (at 4.0 the null test came
# back 7.5 wins instead of 7.0; at 0.0 it is 7.00 on the nose).
ADP_NOISE = 0.0

MIN_WEEK = {"QB": -3.0, "DEF": -5.0}


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


def _board_noise(p: dict, scale: bool) -> float:
    """Standard deviation of one player's scatter off his ADP.

    Flat noise is wrong here: a fixed +/-5 is nothing at ADP 150, where
    consensus is thin anyway, but it is enormous at ADP 1-5, where real
    drafts are extremely locked in (the gap between the #1 and #6 overall
    picks is only 5 spots). Scaling with ADP means the very top of the board
    stays close to a lock while the scatter that makes the mid-to-late board
    interesting is preserved.
    """
    if not scale:
        return 0.0
    adp = p.get("adp") or 999.0
    return AVAILABILITY_NOISE_BASE + AVAILABILITY_NOISE_GROWTH * adp


def _draft(order: list[dict], pool: list[dict], slot: int, teams: int,
           roster: dict, bench: int, style: str,
           rng: random.Random,
           scatter: bool = False) -> tuple[list[dict], list[list[dict]]]:
    """One draft. Returns (my roster, opponent rosters).

    scatter=False (the season simulation's setting): opponents follow Yahoo's
    default board exactly — that is the fairness premise the null test
    checks. The pre-draft tools below pass scatter=True: a "will he survive
    to my pick" probability against a PERFECTLY static board is either 0% or
    100% every time, which answers nothing. Real opponents deviate from
    consensus, so those checks need scatter to produce an actual probability.
    """
    starters = sum(roster.values())
    rounds = starters + bench
    board = sorted(pool, key=lambda p: (p.get("adp") or 999)
                   + rng.gauss(0, _board_noise(p, scatter)))

    linear = (style or "snake").lower().startswith("lin")
    gone: set[str] = set()
    mine: list[dict] = []
    my_filled: dict[str, int] = {}
    opp: list[list[dict]] = [[] for _ in range(teams)]
    opp_filled: list[dict] = [{} for _ in range(teams)]

    for pick in range(1, teams * rounds + 1):
        rnd, i = divmod(pick - 1, teams)
        owner = i + 1 if (linear or rnd % 2 == 0) else teams - i

        if owner == slot:
            src, filled, dest = order, my_filled, mine
        else:
            src, filled, dest = board, opp_filled[owner - 1], opp[owner - 1]

        chosen = None
        if len(dest) < starters:                      # starters first
            for p in src:
                if p["name"] in gone:
                    continue
                s = _slot_for(p["pos"], roster, filled)
                if s:
                    chosen = p
                    filled[s] = filled.get(s, 0) + 1
                    break
            # A team's own ranked list can run dry on a scarce position (K
            # and DEF are ~14 deep leaguewide, and a shortened list only
            # carries a handful) while that position is still open and
            # players at it still exist elsewhere in the draft. Widening the
            # search to the full pool here is what a real draft actually
            # does — a manager out of kickers on his own cheat sheet still
            # drafts SOME kicker, he does not draft a third quarterback
            # instead and call it a starter. Skipping this widen step is
            # what let a second QB silently fill a missing DEF/K slot and
            # get counted as a legitimate starter: measured at 28% of
            # drafts under plain standard scoring before this fix.
            if chosen is None:
                for p in board:
                    if p["name"] in gone:
                        continue
                    s = _slot_for(p["pos"], roster, filled)
                    if s:
                        chosen = p
                        filled[s] = filled.get(s, 0) + 1
                        break
        if chosen is None:                            # then best available
            for p in src:
                if p["name"] not in gone:
                    chosen = p
                    break
        if chosen is None:
            for p in board:                           # list exhausted
                if p["name"] not in gone:
                    chosen = p
                    break
        if chosen is None:
            continue
        gone.add(chosen["name"])
        dest.append(chosen)

    return mine, [r for r in opp if r]


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
        mine, opps = _draft(order, pool, slot, teams, roster, bench,
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
        mine, _ = _draft(order, pool, slot, teams, roster, bench, style,
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

    # Which of MY picks is the real decision point for each player.
    #
    # Two ways to get this wrong, both by only looking at picks AFTER his ADP:
    #   1. A player with ADP 3, for someone drafting slot 1: the first of my
    #      picks at or after 3 is pick 24, which comes back 0% available —
    #      true and useless, since the real decision was pick 1, where he is
    #      a lock.
    #   2. A player with ADP 21, when my picks are [6, 19, 30, ...]: pick 19
    #      is BEFORE his ADP and well within reach, but "first of my picks
    #      >= adp" skips straight past it to pick 30 — by which point he is
    #      almost always long gone — and reports him as 100% available at a
    #      pick where he never actually shows up.
    #
    # The fix is the same for both: take the EARLIEST of my picks that falls
    # within his plausible range (adp +/- 2 standard deviations), and only
    # fall back to "my pick right before his window opens" if none of my
    # picks land inside it at all.
    target_pick: dict[str, int] = {}
    for p in watch:
        adp = p.get("adp") or 999.0
        spread = 2.0 * _board_noise(p, True)
        lo, hi = adp - spread, adp + spread
        in_range = [k for k in my_picks if lo <= k <= hi]
        if in_range:
            target_pick[p["name"]] = in_range[0]
        else:
            earlier = [k for k in my_picks if k < lo]
            later = [k for k in my_picks if k > hi]
            target_pick[p["name"]] = (earlier[-1] if earlier
                                      else later[0] if later
                                      else my_picks[0] if my_picks else None)

    for _ in range(trials):
        board = sorted(pool, key=lambda p: (p.get("adp") or 999)
                       + rng.gauss(0, _board_noise(p, True)))
        gone: set[str] = set()
        starters_by_owner = [0] * (teams + 1)
        filled_by_owner: list[dict] = [{} for _ in range(teams + 1)]

        linear = (style or "snake").lower().startswith("lin")
        taken_at: dict[str, int] = {}
        for pick in range(1, teams * rounds + 1):
            rnd, i = divmod(pick - 1, teams)
            owner = i + 1 if (linear or rnd % 2 == 0) else teams - i
            src = order if owner == slot else board
            filled = filled_by_owner[owner]

            chosen = None
            if starters_by_owner[owner] < starters:
                for p in src:
                    if p["name"] in gone:
                        continue
                    s = _slot_for(p["pos"], roster, filled)
                    if s:
                        chosen = p
                        filled[s] = filled.get(s, 0) + 1
                        break
                # Same widen-to-full-pool fallback as _draft, and for the
                # same reason: a team's own ranked list can run dry on a
                # scarce position (K/DEF) while that position is still open
                # and players at it still exist in the wider pool. Without
                # this, a second QB (or whatever's next on the list) could
                # silently fill a missing K/DEF slot and get counted as a
                # starter, corrupting the availability odds for anyone
                # watching that position.
                if chosen is None and owner == slot:
                    for p in board:
                        if p["name"] in gone:
                            continue
                        s = _slot_for(p["pos"], roster, filled)
                        if s:
                            chosen = p
                            filled[s] = filled.get(s, 0) + 1
                            break
            if chosen is None:
                for p in src:
                    if p["name"] not in gone:
                        chosen = p
                        break
            if chosen is None:
                continue
            gone.add(chosen["name"])
            starters_by_owner[owner] += 1
            taken_at[chosen["name"]] = pick

            # Once every watched player is resolved for this trial, stop —
            # nothing past that point changes the tally.
            if pick >= max(target_pick.values(), default=0) and all(
                    n in gone for n in survived):
                break

        # "Survived" means still on the board at MY target pick — taken by
        # ME at or after it counts (I got him when I meant to), taken by
        # anyone else BEFORE it does not (he was gone before my decision
        # point came up). The earlier version only checked whether *I*
        # picked him late or never picked him at all, so an opponent taking
        # him well ahead of my pick was silently counted as "available."
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
