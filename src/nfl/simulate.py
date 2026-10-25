"""Monte Carlo of the rest of a season: standings, seeds, and the playoff path.

Each simulation plays every remaining regular-season game from the current
ratings, applies the real tiebreakers to seed both conferences, then plays the
bracket. Ratings move inside a simulation as its games are played ("hot"
simulation): a team that wins its next three in one simulated world should be
rated higher for the fourth. Without that, simulations understate how far a
season can drift and the extremes come out too unlikely.

A margin is drawn from a normal around the rating's expected margin, so
tiebreakers that use points have something to work with. A drawn margin of 0
is resolved by a coin flip: ties occur in about 0.3% of games and modelling
them would buy nothing here.

Games already played — in the regular season or the postseason — are never
re-simulated: a played playoff game between the same two clubs is taken as
decided.

The output is plain counts, so independent chunks (different seeds) are
merged by adding them; :func:`merge` is that addition.
"""

from __future__ import annotations

import random
from collections import Counter, defaultdict
from dataclasses import dataclass

from .elo import ELO_PER_POINT, MARGIN_SD, EloParams, game_diff, update, win_prob
from .games import Game
from .standings import Standings, seed_conference
from .teams import ALL_TEAMS, FIRST_ALIGNED_SEASON, conference_of, playoff_teams_per_conference

ROUNDS = (
    "playoffs",
    "division",
    "bye",
    "divisional_round",
    "conference_game",
    "super_bowl",
    "champion",
)


@dataclass
class SeasonState:
    """Everything a simulation needs, captured once so every chunk agrees."""

    season: int
    ratings: dict[str, float]
    played: list[tuple[str, str, int]]  # regular-season results so far
    remaining: list[Game]  # unplayed regular-season games, in order
    postseason: dict[frozenset, tuple[str, int]]  # {home, away} -> (home, margin)
    params: EloParams

    def to_dict(self) -> dict:
        return {
            "season": self.season,
            "ratings": self.ratings,
            "played": [list(r) for r in self.played],
            "remaining": [
                {
                    "game_id": g.game_id,
                    "home": g.home,
                    "away": g.away,
                    "neutral": g.neutral,
                    "home_rest": g.home_rest,
                    "away_rest": g.away_rest,
                }
                for g in self.remaining
            ],
            "postseason": [[h, a, m] for (h, m), a in _post_rows(self.postseason)],
            "params": self.params.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> SeasonState:
        remaining = [
            Game(
                game_id=r["game_id"],
                season=d["season"],
                game_type="REG",
                week=0,
                gameday="",
                gametime="",
                home=r["home"],
                away=r["away"],
                home_score=None,
                away_score=None,
                neutral=r["neutral"],
                home_rest=r["home_rest"],
                away_rest=r["away_rest"],
                spread_line=None,
            )
            for r in d["remaining"]
        ]
        post = {frozenset((h, a)): (h, m) for h, a, m in d["postseason"]}
        return cls(
            season=d["season"],
            ratings=dict(d["ratings"]),
            played=[tuple(r) for r in d["played"]],
            remaining=remaining,
            postseason=post,
            params=EloParams(**d["params"]),
        )


def _post_rows(post: dict[frozenset, tuple[str, int]]):
    for key, (home, margin) in post.items():
        (away,) = key - {home}
        yield (home, margin), away


def season_state(
    games: list[Game], ratings: dict[str, float], season: int, params: EloParams
) -> SeasonState:
    if season < FIRST_ALIGNED_SEASON:
        raise ValueError(f"the simulator supports {FIRST_ALIGNED_SEASON} onwards, not {season}")
    this = [g for g in games if g.season == season]
    if not this:
        raise ValueError(f"no schedule for {season}")
    return SeasonState(
        season=season,
        ratings={t: ratings.get(t, params.mean) for t in ALL_TEAMS},
        played=[(g.home, g.away, g.margin) for g in this if g.game_type == "REG" and g.played],
        remaining=sorted(
            (g for g in this if g.game_type == "REG" and not g.played), key=lambda g: g.sort_key
        ),
        postseason={
            frozenset((g.home, g.away)): (g.home, g.margin)
            for g in this
            if g.postseason and g.played
        },
        params=params,
    )


def _draw_margin(rng: random.Random, diff: float) -> int:
    m = round(rng.gauss(diff / ELO_PER_POINT, MARGIN_SD))
    if m == 0:
        m = 1 if rng.random() < 0.5 else -1
    return m


def _play(
    state: SeasonState,
    r: dict[str, float],
    home: str,
    away: str,
    rng: random.Random,
    neutral: bool = False,
    home_bye: bool = False,
) -> str:
    """Play one postseason game (or take the real result); returns the winner."""
    decided = state.postseason.get(frozenset((home, away)))
    if decided:
        real_home, margin = decided
        return real_home if margin > 0 else ({home, away} - {real_home}).pop()
    p = state.params
    diff = r[home] - r[away] + (0 if neutral else p.hfa) + (p.bye_bonus if home_bye else 0)
    diff *= p.playoff_mult
    margin = _draw_margin(rng, diff)
    r[home], r[away] = update(r[home], r[away], diff, margin, p.k)
    return home if margin > 0 else away


def _bracket(
    state: SeasonState, seeds: list[str], r: dict[str, float], rng: random.Random, tally
) -> str:
    """Play one conference's bracket; returns its champion."""
    n = len(seeds)
    byes = 1 if n == 7 else 2
    alive = list(range(byes))  # seed indexes still playing
    for hi in range(byes, (n + byes) // 2):
        lo = n - 1 - (hi - byes)
        winner = _play(state, r, seeds[hi], seeds[lo], rng)
        alive.append(seeds.index(winner))
    for i in alive:
        tally[seeds[i]]["divisional_round"] += 1
    # Divisional round: the top seed hosts the lowest seed left.
    alive.sort()
    pairs = [(alive[0], alive[3]), (alive[1], alive[2])]
    finalists = []
    for hi, lo in pairs:
        w = _play(state, r, seeds[hi], seeds[lo], rng, home_bye=hi < byes)
        finalists.append(seeds.index(w))
    for i in finalists:
        tally[seeds[i]]["conference_game"] += 1
    finalists.sort()
    champ = _play(state, r, seeds[finalists[0]], seeds[finalists[1]], rng)
    tally[champ]["super_bowl"] += 1
    return champ


def simulate(state: SeasonState, sims: int, seed: int) -> dict:
    """Run ``sims`` simulations; returns mergeable counts."""
    rng = random.Random(seed)
    p = state.params
    tally: dict[str, Counter] = defaultdict(Counter)
    seed_counts: dict[str, Counter] = defaultdict(Counter)
    wins_total: Counter = Counter()
    matchups: Counter = Counter()
    n_playoff = playoff_teams_per_conference(state.season)

    for _ in range(sims):
        r = dict(state.ratings)
        results = list(state.played)
        for g in state.remaining:
            diff = game_diff(r[g.home], r[g.away], g, p)
            margin = _draw_margin(rng, diff)
            r[g.home], r[g.away] = update(r[g.home], r[g.away], diff, margin, p.k)
            results.append((g.home, g.away, margin))
        st = Standings(results)
        for t in ALL_TEAMS:
            wins_total[t] += st.record[t][0]
        champs = {}
        for conf in ("AFC", "NFC"):
            seeds, _ = seed_conference(st, conf, state.season, rng)
            for i, t in enumerate(seeds):
                seed_counts[t][str(i + 1)] += 1
                tally[t]["playoffs"] += 1
                if i < 4:
                    tally[t]["division"] += 1
                if i < (1 if n_playoff == 7 else 2):
                    tally[t]["bye"] += 1
            champs[conf] = _bracket(state, seeds, r, rng, tally)
        matchups[f"{champs['AFC']}-{champs['NFC']}"] += 1
        champ = _play(state, r, champs["AFC"], champs["NFC"], rng, neutral=True)
        tally[champ]["champion"] += 1

    return {
        "season": state.season,
        "sims": sims,
        "teams": {
            t: {
                **{k: tally[t][k] for k in ROUNDS},
                "seeds": dict(seed_counts[t]),
                "wins_total": wins_total[t],
            }
            for t in ALL_TEAMS
        },
        "matchups": dict(matchups),
    }


def merge(parts: list[dict]) -> dict:
    """Add chunk counts together."""
    parts = [p for p in parts if p]
    if not parts:
        raise ValueError("nothing to merge")
    seasons = {p["season"] for p in parts}
    if len(seasons) != 1:
        raise ValueError(f"chunks from different seasons: {sorted(seasons)}")
    out = {"season": parts[0]["season"], "sims": 0, "teams": {}, "matchups": Counter()}
    for p in parts:
        out["sims"] += p["sims"]
        out["matchups"].update(p["matchups"])
        for t, c in p["teams"].items():
            o = out["teams"].setdefault(
                t, dict.fromkeys(ROUNDS, 0) | {"seeds": Counter(), "wins_total": 0}
            )
            for k in ROUNDS:
                o[k] += c[k]
            o["wins_total"] += c["wins_total"]
            o["seeds"].update(c["seeds"])
    for o in out["teams"].values():
        o["seeds"] = dict(o["seeds"])
    out["matchups"] = dict(out["matchups"])
    return out


def odds(merged: dict) -> list[dict]:
    """Per-team probabilities from merged counts, best Super Bowl odds first."""
    n = merged["sims"]
    rows = []
    for t, c in merged["teams"].items():
        rows.append(
            {
                "team": t,
                "conference": conference_of(t),
                "mean_wins": round(c["wins_total"] / n, 2),
                **{k: round(c[k] / n, 4) for k in ROUNDS},
                "seeds": {s: round(v / n, 4) for s, v in sorted(c["seeds"].items())},
            }
        )
    rows.sort(key=lambda x: (-x["champion"], -x["super_bowl"], -x["playoffs"], x["team"]))
    return rows


def projected_bracket(merged: dict, season: int) -> dict[str, list[dict]]:
    """The likeliest seeding per conference: each seed's most frequent holder.

    Assigned seed by seed without reusing a team, so it is always a valid
    bracket — but it is a SUMMARY, not a forecast that this exact bracket will
    occur (the likeliest single bracket is usually itself unlikely).
    """
    n = merged["sims"]
    n_playoff = playoff_teams_per_conference(season)
    out: dict[str, list[dict]] = {}
    for conf in ("AFC", "NFC"):
        used: set[str] = set()
        seeds = []
        for s in range(1, n_playoff + 1):
            best = max(
                (
                    t
                    for t, c in merged["teams"].items()
                    if conference_of(t) == conf and t not in used
                ),
                key=lambda t: (
                    merged["teams"][t]["seeds"].get(str(s), 0),
                    merged["teams"][t]["playoffs"],
                ),
            )
            used.add(best)
            seeds.append(
                {
                    "seed": s,
                    "team": best,
                    "p": round(merged["teams"][best]["seeds"].get(str(s), 0) / n, 4),
                }
            )
        out[conf] = seeds
    return out


__all__ = [
    "SeasonState",
    "merge",
    "odds",
    "projected_bracket",
    "season_state",
    "simulate",
    "win_prob",
]
