"""The season simulator: invariants that must hold in every simulated world."""

from __future__ import annotations

import pytest

from nfl.elo import EloParams, rate
from nfl.games import parse_games_csv
from nfl.simulate import SeasonState, merge, odds, projected_bracket, season_state, simulate

from .conftest import synthetic_games_csv


@pytest.fixture(scope="module")
def state() -> SeasonState:
    games = parse_games_csv(synthetic_games_csv())
    p = EloParams()
    return season_state(games, rate(games, p).ratings, 2025, p)


def test_every_world_has_a_full_field_and_one_champion(state):
    out = simulate(state, 200, seed=1)
    t = out["teams"]
    assert sum(c["playoffs"] for c in t.values()) == 14 * 200
    assert sum(c["division"] for c in t.values()) == 8 * 200
    assert sum(c["bye"] for c in t.values()) == 2 * 200
    assert sum(c["super_bowl"] for c in t.values()) == 2 * 200
    assert sum(c["champion"] for c in t.values()) == 200
    assert sum(out["matchups"].values()) == 200


def test_same_seed_repeats_and_chunks_merge_by_addition(state):
    a = simulate(state, 100, seed=5)
    assert simulate(state, 100, seed=5) == a
    b = simulate(state, 100, seed=6)
    m = merge([a, b])
    assert m["sims"] == 200
    team = next(iter(a["teams"]))
    assert (
        m["teams"][team]["playoffs"] == a["teams"][team]["playoffs"] + b["teams"][team]["playoffs"]
    )


def test_state_round_trips_through_json(state):
    again = SeasonState.from_dict(state.to_dict())
    assert simulate(again, 50, seed=3) == simulate(state, 50, seed=3)


def test_merge_refuses_mixed_seasons(state):
    a = simulate(state, 10, seed=1)
    with pytest.raises(ValueError):
        merge([a, {**a, "season": 2024}])


def test_odds_and_bracket_are_consistent(state):
    m = merge([simulate(state, 300, seed=2)])
    rows = odds(m)
    assert abs(sum(r["champion"] for r in rows) - 1.0) < 1e-3  # each row is rounded to 4 places
    for r in rows:
        assert (
            r["champion"]
            <= r["super_bowl"]
            <= r["conference_game"]
            <= r["divisional_round"]
            <= r["playoffs"]
        )
    bracket = projected_bracket(m, 2025)
    for seeds in bracket.values():
        assert [s["seed"] for s in seeds] == list(range(1, 8))
        assert len({s["team"] for s in seeds}) == 7


def test_a_played_playoff_game_is_never_resimulated():
    games = parse_games_csv(synthetic_games_csv(played_weeks_last=6))
    p = EloParams()
    st = season_state(games, rate(games, p).ratings, 2025, p)
    assert not st.remaining
    # Find the real 2-v-7 AFC pairing in one world, then declare the 7 seed the winner.
    probe = simulate(st, 1, seed=0)
    seven = next(
        t
        for t, c in probe["teams"].items()
        if c["seeds"].get("7")
        and t
        in {
            "BUF",
            "MIA",
            "NE",
            "NYJ",
            "BAL",
            "CIN",
            "CLE",
            "PIT",
            "HOU",
            "IND",
            "JAX",
            "TEN",
            "DEN",
            "KC",
            "LV",
            "LAC",
        }
    )
    two = next(
        t
        for t, c in probe["teams"].items()
        if c["seeds"].get("2")
        and t
        in {
            "BUF",
            "MIA",
            "NE",
            "NYJ",
            "BAL",
            "CIN",
            "CLE",
            "PIT",
            "HOU",
            "IND",
            "JAX",
            "TEN",
            "DEN",
            "KC",
            "LV",
            "LAC",
        }
    )
    st.postseason[frozenset((two, seven))] = (two, -10)
    out = simulate(st, 200, seed=9)
    assert out["teams"][seven]["divisional_round"] == 200
    assert out["teams"][two]["divisional_round"] == 0


def test_rejects_seasons_before_the_2002_alignment():
    games = parse_games_csv(synthetic_games_csv(seasons=(2000, 2001)))
    with pytest.raises(ValueError):
        season_state(games, {}, 2001, EloParams())
