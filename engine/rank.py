"""Blended pool -> a draft list that survives Yahoo autodraft.

Skill positions sort by edge over replacement (what you gain vs the best
free player at that position). Kickers and defenses are placed by round,
four deep and adjacent: opponents drafting the default list take them
around their ADP, so a two-deep list loses both before your pick comes.
That was measured, not guessed (18 of 36 test drafts finished with no
kicker at two-deep; one of 36 at four-deep).

That placement is scoring-BLIND by default, which is fine most of the time
(field goals and points-allowed tiers do not depend on the league's yardage
rates, so a kicker's or defense's raw value barely moves league to league)
but wrong for leagues that compress skill-position scoring, like a
halved-yardage format (HFL): K/DEF's own points do not change, but everyone
AROUND them scores less, so the same kicker or defense is relatively much
more valuable there than in a standard league. Burying him at a fixed round
regardless would silently throw away a real edge.

`compression` (engine.blend.skill_compression) is how the caller tells this
function that shift happened: a single ratio, 1.0 in an ordinary half-PPR
league, well under 1.0 in a league like HFL where skill scoring is
depressed. Below that ratio, K/DEF move earlier than the default placement,
capped so this never fully reopens the sniping problem the placement rule
exists to prevent.
"""
from __future__ import annotations

from .vorp import add_vorp

TARGET_ROUND_FRAC = {"K": 0.80, "DEF": 0.87}
KEEP = {"K": 4, "DEF": 4}

# Below this compression ratio, K/DEF start moving earlier. 1.0 is the
# site's own half-PPR default; leagues within ~10% of it (full PPR runs
# ~1.11, bare 0-PPR standard ~0.89) are ordinary variation, not the kind of
# structural compression a halved-yardage league produces (~0.71 for HFL),
# so nothing should move for them.
COMPRESSION_THRESHOLD = 0.85

# How much of the gap between "where the safety placement puts him" and
# "where his points would actually rank him among skill players" gets
# closed, scaled by how far under the threshold this league's compression
# sits. A league right at the threshold barely moves him; HFL's ~0.71 moves
# him roughly halfway. Never fully to his value-only rank, so most of the
# placement rule's protection against a run stays in force regardless of
# how compressed the league is.
MAX_PULL_FRACTION = 0.6


def build_list(pool: list[dict], teams: int, roster: dict,
               bench: int, compression: float = 1.0) -> list[dict]:
    add_vorp(pool, teams, roster)
    rounds = sum(roster.values()) + bench

    skill = [p for p in pool if p["pos"] not in TARGET_ROUND_FRAC]
    skill.sort(key=lambda p: -p.get("vorp", 0))
    out = list(skill)

    pull_fraction = 0.0
    if compression < COMPRESSION_THRESHOLD:
        pull_fraction = min(MAX_PULL_FRACTION,
                            (COMPRESSION_THRESHOLD - compression)
                            / COMPRESSION_THRESHOLD * 2)

    for pos, frac in TARGET_ROUND_FRAC.items():
        group = sorted([p for p in pool if p["pos"] == pos],
                       key=lambda p: -p["pts"])[: KEEP.get(pos, 4)]
        if not group:
            continue

        base_target = min(int(rounds * frac * teams), len(out))

        if pull_fraction > 0:
            # Where the best of the group would land if slotted purely by
            # points among the skill players already placed — i.e. the rank
            # the safety placement is deliberately overriding — and the
            # whole KEEP-deep group shifts together, since adjacency (not
            # any one player's exact rank) is what protects against a
            # one-pick snipe.
            best_pts = group[0]["pts"]
            value_rank = sum(1 for p in out if p["pts"] > best_pts)
            target = base_target - round(
                (base_target - value_rank) * pull_fraction)
            target = max(value_rank, min(target, base_target))
        else:
            target = base_target

        for i, p in enumerate(group):
            out.insert(min(target + i, len(out)), p)

    for i, p in enumerate(out, 1):
        p["rank"] = i
    return out
