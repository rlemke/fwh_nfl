"""Standings, the NFL tiebreaking procedures, and playoff seeding.

Implements the league's published procedures (2002 alignment onwards):

**Two or more clubs in a division** — head-to-head among them, division
record, record in common games, conference record, strength of victory,
strength of schedule, net points in common games, net points in all games,
coin toss.

**Wild card / seeding across divisions** — first drop every club that is not
the highest-ranked of its division; then for two clubs: head-to-head (if they
met), conference record, common games (minimum four), strength of victory,
strength of schedule, net points in conference games, net points in all
games, coin toss. For three or more, head-to-head applies only as a SWEEP (one
club beat each of the others, or lost to each of them).

Whenever a step separates SOME of the tied clubs, the rest go back to step 1
— the league's "revert" rule — which is why every procedure here selects one
club at a time and restarts.

⚠️ Not implemented: the two "combined ranking of points scored and allowed"
steps, which sit between strength of schedule and net points. They decide a
tie very rarely; when they would have, this code falls to net points instead.
:func:`seed_conference` is checked against every real postseason since 2002 in
the tests, which is how "rarely" is measured rather than assumed.
"""

from __future__ import annotations

import random
from collections import defaultdict
from collections.abc import Callable, Iterable

from .teams import (
    ALL_TEAMS,
    DIVISIONS,
    TEAM_CONFERENCE,
    TEAM_DIVISION,
    playoff_teams_per_conference,
)

Result = tuple[str, str, int]  # (home, away, home margin)


def _pct(w: float, games: int) -> float:
    return w / games if games else 0.0


class Standings:
    """Records derived from a season's regular-season results."""

    def __init__(self, results: Iterable[Result], teams: Iterable[str] = ALL_TEAMS):
        self.teams = list(teams)
        self.wins: dict[str, float] = defaultdict(float)  # a tie counts half
        self.games: dict[str, int] = defaultdict(int)
        self.div_w: dict[str, float] = defaultdict(float)
        self.div_g: dict[str, int] = defaultdict(int)
        self.conf_w: dict[str, float] = defaultdict(float)
        self.conf_g: dict[str, int] = defaultdict(int)
        self.conf_net: dict[str, int] = defaultdict(int)
        self.net: dict[str, int] = defaultdict(int)
        # (team, opponent) -> [points from those games (W=1, T=.5), games, net]
        self.vs: dict[tuple[str, str], list] = defaultdict(lambda: [0.0, 0, 0])
        self.opponents: dict[str, list[str]] = defaultdict(list)
        self.beaten: dict[str, list[str]] = defaultdict(list)
        self.record: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])  # W, L, T
        for home, away, margin in results:
            self._add(home, away, margin)
            self._add(away, home, -margin)

    def _add(self, team: str, opp: str, margin: int) -> None:
        pts = 1.0 if margin > 0 else 0.0 if margin < 0 else 0.5
        self.record[team][0 if margin > 0 else 1 if margin < 0 else 2] += 1
        self.wins[team] += pts
        self.games[team] += 1
        self.net[team] += margin
        if TEAM_DIVISION[team] == TEAM_DIVISION[opp]:
            self.div_w[team] += pts
            self.div_g[team] += 1
        if TEAM_CONFERENCE[team] == TEAM_CONFERENCE[opp]:
            self.conf_w[team] += pts
            self.conf_g[team] += 1
            self.conf_net[team] += margin
        v = self.vs[(team, opp)]
        v[0] += pts
        v[1] += 1
        v[2] += margin
        self.opponents[team].append(opp)
        if margin > 0:
            self.beaten[team].append(opp)

    # ---- records -------------------------------------------------------

    def pct(self, team: str) -> float:
        return _pct(self.wins[team], self.games[team])

    def div_pct(self, team: str) -> float:
        return _pct(self.div_w[team], self.div_g[team])

    def conf_pct(self, team: str) -> float:
        return _pct(self.conf_w[team], self.conf_g[team])

    def _combined(self, teams: list[str]) -> float:
        return _pct(sum(self.wins[t] for t in teams), sum(self.games[t] for t in teams))

    def sov(self, team: str) -> float:
        """Strength of victory: combined record of the teams it beat."""
        return self._combined(self.beaten[team])

    def sos(self, team: str) -> float:
        """Strength of schedule: combined record of every opponent."""
        return self._combined(self.opponents[team])

    def h2h(self, team: str, others: Iterable[str]) -> tuple[float, int]:
        w = g = 0
        for o in others:
            if o != team:
                v = self.vs.get((team, o))
                if v:
                    w += v[0]
                    g += v[1]
        return w, g

    def common_opponents(self, group: list[str]) -> set[str]:
        sets = [set(self.opponents[t]) for t in group]
        return set.intersection(*sets) - set(group) if sets else set()

    def common(self, team: str, common: set[str]) -> tuple[float, int, int]:
        w = g = net = 0
        for o in common:
            v = self.vs.get((team, o))
            if v:
                w += v[0]
                g += v[1]
                net += v[2]
        return w, g, net


Step = Callable[[Standings, list[str]], dict[str, float] | None]


def _h2h_step(st: Standings, group: list[str]) -> dict[str, float] | None:
    vals = {}
    for t in group:
        w, g = st.h2h(t, group)
        vals[t] = _pct(w, g)
    return vals


def _h2h_if_met(st: Standings, group: list[str]) -> dict[str, float] | None:
    """Wild-card head-to-head: two clubs only if they met; 3+ only a sweep."""
    if len(group) == 2:
        a, b = group
        v = st.vs.get((a, b))
        if not v:
            return None
        return {a: _pct(v[0], v[1]), b: _pct(st.vs[(b, a)][0], v[1])}
    for t in group:
        others = [o for o in group if o != t]
        if all(st.vs.get((t, o)) for o in others):
            if all(st.vs[(t, o)][0] == st.vs[(t, o)][1] for o in others):
                return {x: 1.0 if x == t else 0.0 for x in group}  # swept them
            if all(st.vs[(t, o)][0] == 0 for o in others):
                return {x: 0.0 if x == t else 1.0 for x in group}  # was swept
    return None


def _div_step(st: Standings, group: list[str]) -> dict[str, float]:
    return {t: st.div_pct(t) for t in group}


def _conf_step(st: Standings, group: list[str]) -> dict[str, float]:
    return {t: st.conf_pct(t) for t in group}


def _common_step(min_games: int) -> Step:
    def step(st: Standings, group: list[str]) -> dict[str, float] | None:
        common = st.common_opponents(group)
        vals = {}
        for t in group:
            w, g, _ = st.common(t, common)
            if g < min_games:
                return None
            vals[t] = _pct(w, g)
        return vals

    return step


def _sov_step(st: Standings, group: list[str]) -> dict[str, float]:
    return {t: st.sov(t) for t in group}


def _sos_step(st: Standings, group: list[str]) -> dict[str, float]:
    return {t: st.sos(t) for t in group}


def _net_common_step(st: Standings, group: list[str]) -> dict[str, float]:
    common = st.common_opponents(group)
    return {t: float(st.common(t, common)[2]) for t in group}


def _net_conf_step(st: Standings, group: list[str]) -> dict[str, float]:
    return {t: float(st.conf_net[t]) for t in group}


def _net_step(st: Standings, group: list[str]) -> dict[str, float]:
    return {t: float(st.net[t]) for t in group}


DIVISION_STEPS: list[Step] = [
    _h2h_step,
    _div_step,
    _common_step(0),
    _conf_step,
    _sov_step,
    _sos_step,
    _net_common_step,
    _net_step,
]

WILDCARD_STEPS: list[Step] = [
    _h2h_if_met,
    _conf_step,
    _common_step(4),
    _sov_step,
    _sos_step,
    _net_conf_step,
    _net_step,
]


def _pick(
    st: Standings,
    group: list[str],
    steps: list[Step],
    rng: random.Random,
    reduce: Callable[[list[str]], list[str]] | None = None,
) -> str:
    """The single club that comes out on top of a tied ``group``."""
    if reduce:
        group = reduce(group)
    if len(group) == 1:
        return group[0]
    for step in steps:
        vals = step(st, group)
        if vals is None:
            continue
        best = max(round(v, 9) for v in vals.values())
        top = [t for t in group if round(vals[t], 9) == best]
        if len(top) == 1:
            return top[0]
        if len(top) < len(group):
            return _pick(st, top, steps, rng, reduce)  # revert to step 1
    return rng.choice(sorted(group))  # coin toss


def _order(
    st: Standings,
    teams: list[str],
    steps: list[Step],
    rng: random.Random,
    reduce: Callable[[list[str]], list[str]] | None = None,
) -> list[str]:
    """Rank ``teams``: best record first, ties broken one club at a time."""
    remaining = list(teams)
    out: list[str] = []
    while remaining:
        best = max(round(st.pct(t), 9) for t in remaining)
        tied = [t for t in remaining if round(st.pct(t), 9) == best]
        pick = tied[0] if len(tied) == 1 else _pick(st, tied, steps, rng, reduce)
        out.append(pick)
        remaining.remove(pick)
    return out


def rank_division(st: Standings, division: str, rng: random.Random) -> list[str]:
    return _order(st, DIVISIONS[division], DIVISION_STEPS, rng)


def seed_conference(
    st: Standings, conference: str, season: int, rng: random.Random | None = None
) -> tuple[list[str], dict[str, list[str]]]:
    """Playoff seeds for one conference, best first, and each division's order."""
    rng = rng or random.Random(0)
    div_orders = {d: rank_division(st, d, rng) for d in DIVISIONS if d.startswith(conference)}
    div_rank = {t: i for order in div_orders.values() for i, t in enumerate(order)}

    def best_per_division(group: list[str]) -> list[str]:
        best: dict[str, str] = {}
        for t in group:
            d = TEAM_DIVISION[t]
            if d not in best or div_rank[t] < div_rank[best[d]]:
                best[d] = t
        return [t for t in group if best[TEAM_DIVISION[t]] == t]

    winners = [order[0] for order in div_orders.values()]
    seeds = _order(st, winners, WILDCARD_STEPS, rng)
    others = [t for order in div_orders.values() for t in order[1:]]
    wildcards = _order(st, others, WILDCARD_STEPS, rng, best_per_division)
    n = playoff_teams_per_conference(season)
    return seeds + wildcards[: n - len(seeds)], div_orders
