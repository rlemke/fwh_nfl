"""Ratings, backtest scoring and the market conversion."""

from __future__ import annotations

import pytest

from nfl.backtest import Score, backtest
from nfl.elo import EloParams, game_diff, rate, spread_to_prob, update, win_prob
from nfl.games import Game, parse_games_csv

from .conftest import synthetic_games_csv


def _game(**kw) -> Game:
    base = dict(  # noqa: C408 — keyword form reads as the Game fields
        game_id="g", season=2025, game_type="REG", week=1, gameday="2025-09-07", gametime="13:00",
        home="KC", away="BUF", home_score=None, away_score=None, neutral=False,
        home_rest=7, away_rest=7, spread_line=None,
    )  # fmt: skip
    base.update(kw)
    return Game(**base)


def test_win_prob_is_symmetric_and_even_at_zero():
    assert win_prob(0) == 0.5
    assert win_prob(100) + win_prob(-100) == pytest.approx(1.0)
    assert win_prob(100) == pytest.approx(0.640, abs=0.001)


def test_spread_to_prob_reads_the_home_side():
    assert spread_to_prob(0) == 0.5
    assert spread_to_prob(3) > 0.5 > spread_to_prob(-3)
    assert spread_to_prob(7) == pytest.approx(0.6986, abs=0.001)


def test_update_is_zero_sum_and_rewards_the_upset_more():
    h, a = update(1500, 1500, 0, 7, 20)
    assert h + a == pytest.approx(3000)
    upset_gain = update(1400, 1600, -200, 7, 20)[0] - 1400
    expected_gain = update(1600, 1400, 200, 7, 20)[0] - 1600
    assert upset_gain > expected_gain > 0


def test_game_diff_applies_home_bye_neutral_and_playoffs():
    p = EloParams()
    assert game_diff(1500, 1500, _game(), p) == p.hfa
    assert game_diff(1500, 1500, _game(neutral=True), p) == 0
    assert game_diff(1500, 1500, _game(home_rest=14), p) == p.hfa + p.bye_bonus
    assert game_diff(1500, 1500, _game(game_type="DIV"), p) == p.hfa * p.playoff_mult


def test_unplayed_games_are_predicted_but_do_not_move_ratings():
    games = parse_games_csv(synthetic_games_csv())
    run = rate(games)
    unplayed = [rg for rg in run.games if not rg.game.played]
    assert unplayed and all(rg.home_elo_post is None for rg in unplayed)
    last = [rg for rg in run.games if rg.game.played][-1]
    assert run.ratings[last.game.home] == last.home_elo_post


def test_ratings_regress_between_seasons():
    games = parse_games_csv(synthetic_games_csv())
    p = EloParams(regress=0.5)
    run = rate(games, p)
    end_2024 = {}
    for rg in run.games:
        if rg.game.season == 2024 and rg.game.played:
            end_2024[rg.game.home], end_2024[rg.game.away] = rg.home_elo_post, rg.away_elo_post
    first_2025 = next(rg for rg in run.games if rg.game.season == 2025)
    team = first_2025.game.home
    assert first_2025.home_elo == pytest.approx(end_2024[team] - 0.5 * (end_2024[team] - p.mean))


def test_score_counts_ties_as_half_and_brier_of_a_coin_is_a_quarter():
    s = Score()
    s.add(0.5, 0.0, 3)
    s.add(0.7, 3.0, 0)
    out = s.summary()
    assert out["accuracy"] == pytest.approx(0.5)
    assert out["brier"] == pytest.approx((0.25 + 0.04) / 2, abs=1e-4)


def test_backtest_compares_model_and_market_on_the_same_games():
    games = parse_games_csv(synthetic_games_csv())
    out = backtest(games, 2024, 2025)
    assert out["model"]["n"] == out["market"]["n"] > 0


def test_real_history_model_is_in_the_expected_range(real_games_csv):
    """The published claim: ~64% out of sample, a little behind the market."""
    out = backtest(parse_games_csv(real_games_csv), 2015, 2025)
    assert 0.62 < out["model"]["accuracy"] < 0.67
    assert out["market"]["brier"] < out["model"]["brier"] < 0.225
