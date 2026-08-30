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
  * waiver moves: when a lineup slot cannot be filled, the team signs the
    best free agent at that position and drops its worst spare player.
    Every team does this, yours and theirs, so the comparison stays fair.

The fairness check that keeps this honest: hand the simulator Yahoo's own
default list as "your" list and it must win about half its games. It does
(7.0 of 14, within noise). Any edge shown for a custom list is earned.
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


def _slot_for(pos: str, roster: dict, filled: dict) -> str | None:
    if roster.get(pos, 0) > filled.get(pos, 0):
        return pos
    for slot, count in roster.items():
        if "/" in slot and pos in slot.upper().split("/") \
                and count > filled.get(slot, 0):
            return slot
    return None


def _draft(order: list[dict], pool: list[dict], slot: int, teams: int,
           roster: dict, bench: int, style: str,
           rng: random.Random) -> tuple[list[dict], list[list[dict]]]:
    """One draft. Returns (my roster, opponent rosters)."""
    starters = sum(roster.values())
    rounds = starters + bench
    board = sorted(pool, key=lambda p: (p.get("adp") or 999)
                   + rng.gauss(0, ADP_NOISE))

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

    # A required slot with no eligible body on the roster gets a waiver add:
    # best real free agent at that position if one exists, otherwise a
    # replacement-level pickup, because in a real league there is always SOME
    # kicker on waivers even when our data pool is drafted bare.
    if drops_allowed:
        for pos, need in roster.items():
            if "/" in pos:
                continue
            have = sum(1 for p in available(team) if p["pos"] == pos)
            guard = 0
            while have < need and guard < 3:
                guard += 1
                pool_fa = fa.get(pos) or []
                new = pool_fa.pop(0) if pool_fa else dict(fa["_level"][pos])
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


def run(order: list[dict], pool: list[dict], slot: int, teams: int,
        roster: dict, bench: int, style: str = "snake",
        trials: int = 150, seed: int = 7) -> dict:
    """Seasons for one list. Returns the win distribution and what drove it."""
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
        drafted = {p["name"] for p in mine}
        for r in opps:
            drafted |= {p["name"] for p in r}

        fa: dict = {"_level": {}}
        for p in sorted(pool, key=lambda p: -p["pts"]):
            if p["name"] not in drafted:
                fa.setdefault(p["pos"], []).append(p)
        # Replacement-level stand-in per position, for when the real free
        # agents run out. Worth 80% of the weakest drafted player there.
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

    if not wins_all:
        return {}
    wins_all.sort()
    n = len(wins_all)
    return {
        "trials": n,
        "weeks": REGULAR_WEEKS,
        "meanWins": round(statistics.mean(wins_all), 1),
        "lowWins": wins_all[n // 10],
        "highWins": wins_all[(9 * n) // 10],
        "dist": {w: wins_all.count(w) for w in sorted(set(wins_all))},
        "meanPoints": round(statistics.mean(pts_all)),
        "injuredStartsPerSeason": round(injured_starts / n, 1),
        "waiverAddsPerSeason": round(my_adds / n, 1),
    }
