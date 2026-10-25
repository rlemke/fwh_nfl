"""Elo team ratings for the NFL.

The method is FiveThirtyEight's published NFL Elo, without its quarterback
adjustment:

- the home side gets ``hfa`` rating points (none at a neutral site);
- a team coming off a bye gets ``bye_bonus``;
- in the playoffs the rating difference is multiplied by ``playoff_mult``,
  because better teams win playoff games more often than the regular-season
  gap predicts;
- the update is scaled by the margin of victory, damped when the favourite won
  (otherwise a strong team's rating inflates by beating weak teams by a lot);
- between seasons every rating regresses ``regress`` of the way to the mean.

One rating point is worth 1/25 of a point on the scoreboard, so a 100-point
edge is a 4-point favourite.

⚠️ No quarterback adjustment. The starting quarterback is the biggest single
thing the market prices that this model cannot see — a backup starting is
invisible here until the results arrive. It is the first known limit to check
when the model and the line disagree.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field

from .games import Game

#: Points of scoreboard margin per Elo point.
ELO_PER_POINT = 25.0
#: Standard deviation of an NFL margin around the expected margin, in points.
#: The usual published figure; it turns a spread into a win probability.
MARGIN_SD = 13.45


@dataclass(frozen=True)
class EloParams:
    """Model constants.

    The defaults were chosen on the 2005–2014 seasons and are reported on
    2015 onwards, so the numbers the site shows are out-of-sample. See
    :func:`nfl.backtest.tune`. Measured 2026-10-25: the best of 60 settings
    (k=20, hfa=48, regress=0.4) had a Brier score of 0.2166 on the training
    window and the worst 0.2192 (k=24, hfa=30, regress=0.25) — a small
    spread, so the constants carry little room to overfit.
    """

    k: float = 20.0
    hfa: float = 48.0
    bye_bonus: float = 25.0
    playoff_mult: float = 1.2
    regress: float = 0.4
    mean: float = 1505.0
    new_team: float = 1300.0  # an expansion team's first rating (HOU 2002)

    def to_dict(self) -> dict:
        return asdict(self)


def win_prob(diff: float) -> float:
    """Probability the side with the ``diff`` rating edge wins."""
    return 1.0 / (1.0 + 10.0 ** (-diff / 400.0))


def spread_to_prob(points: float, sd: float = MARGIN_SD) -> float:
    """Win probability for a side expected to win by ``points``."""
    return 0.5 * (1.0 + math.erf(points / (sd * math.sqrt(2.0))))


def mov_multiplier(margin: int, winner_diff: float) -> float:
    """Margin-of-victory scaling; ``winner_diff`` is the WINNER's rating edge."""
    return math.log(abs(margin) + 1.0) * 2.2 / (winner_diff * 0.001 + 2.2)


def game_diff(home_elo: float, away_elo: float, game: Game, p: EloParams) -> float:
    """The home side's effective rating edge for ``game``."""
    diff = home_elo - away_elo
    if not game.neutral:
        diff += p.hfa
    if game.home_rest is not None and game.home_rest >= 13:
        diff += p.bye_bonus
    if game.away_rest is not None and game.away_rest >= 13:
        diff -= p.bye_bonus
    if game.postseason:
        diff *= p.playoff_mult
    return diff


def update(
    home_elo: float, away_elo: float, diff: float, margin: int, k: float
) -> tuple[float, float]:
    """Ratings after a game the home side won by ``margin`` (negative = lost)."""
    p_home = win_prob(diff)
    if margin > 0:
        actual, mult = 1.0, mov_multiplier(margin, diff)
    elif margin < 0:
        actual, mult = 0.0, mov_multiplier(margin, -diff)
    else:
        actual, mult = 0.5, 1.0
    shift = k * mult * (actual - p_home)
    return home_elo + shift, away_elo - shift


@dataclass
class RatedGame:
    """A game with what the model believed before it was played."""

    game: Game
    home_elo: float
    away_elo: float
    diff: float
    p_home: float
    spread: float  # predicted home margin, in points
    home_elo_post: float | None = None
    away_elo_post: float | None = None


@dataclass
class RatingRun:
    params: EloParams
    games: list[RatedGame] = field(default_factory=list)
    ratings: dict[str, float] = field(default_factory=dict)  # after the last PLAYED game
    season: int = 0  # the season the current ratings belong to


def rate(games: Iterable[Game], params: EloParams | None = None) -> RatingRun:
    """Run the ratings through every game in order.

    Unplayed games are predicted from the ratings as they stand and do not move
    them, so the run's final ratings are always "as of the last result".
    """
    p = params or EloParams()
    run = RatingRun(params=p)
    ratings: dict[str, float] = {}
    first_season: int | None = None
    season: int | None = None

    for g in sorted(games, key=lambda g: g.sort_key):
        if season != g.season:
            if season is not None:
                for team, r in ratings.items():
                    ratings[team] = r - p.regress * (r - p.mean)
            season = g.season
            if first_season is None:
                first_season = g.season
        for team in (g.home, g.away):
            if team not in ratings:
                ratings[team] = p.mean if g.season == first_season else p.new_team

        h, a = ratings[g.home], ratings[g.away]
        diff = game_diff(h, a, g, p)
        rg = RatedGame(g, h, a, diff, win_prob(diff), diff / ELO_PER_POINT)
        if g.played:
            rg.home_elo_post, rg.away_elo_post = update(h, a, diff, g.margin, p.k)  # type: ignore[arg-type]
            ratings[g.home], ratings[g.away] = rg.home_elo_post, rg.away_elo_post
        run.games.append(rg)

    run.ratings = dict(ratings)
    run.season = season or 0
    return run
