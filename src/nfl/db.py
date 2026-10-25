"""MongoDB access for the NFL domain.

Domain data lives in ``FW_EXAMPLES_DATABASE`` (default ``facetwork_examples``),
separate from the FFL runtime database — the convention the stocks, climate
and census dashboard apps share.

Collections:

``nfl_games``          one per game since 1999, with the model's pre-game view
``nfl_teams``          franchise metadata (names, colours, logos)
``nfl_team_ratings``   one per franchise: current rating and its history
``nfl_players``        player bios
``nfl_player_weeks``   one per player per game (a slim subset of nflverse's columns)
``nfl_player_seasons`` the same, summed per player per season and season type
``nfl_predictions``    what the model said BEFORE kickoff — frozen once a game is played
``nfl_sim_inputs``     the exact state a simulation ran from, keyed by its hash
``nfl_odds``           playoff/Super Bowl odds, one per simulation run
``nfl_meta``           ratings run, backtest, and which source files are loaded
"""

from __future__ import annotations

import os

import pymongo

GAMES = "nfl_games"
TEAMS = "nfl_teams"
RATINGS = "nfl_team_ratings"
PLAYERS = "nfl_players"
PLAYER_WEEKS = "nfl_player_weeks"
PLAYER_SEASONS = "nfl_player_seasons"
PREDICTIONS = "nfl_predictions"
SIM_INPUTS = "nfl_sim_inputs"
ODDS = "nfl_odds"
META = "nfl_meta"


def database_name() -> str:
    return os.environ.get("FW_EXAMPLES_DATABASE", "facetwork_examples")


def mongo_url() -> str:
    return (
        os.environ.get("FW_MONGODB_URL")
        or os.environ.get("MONGODB_URL")
        or "mongodb://localhost:27017"
    )


def get_db(client=None):
    """The domain database; pass a client to reuse its connection pool."""
    if client is None:
        client = pymongo.MongoClient(mongo_url())
    return client[database_name()]


def ensure_indexes(db) -> None:
    A, D = pymongo.ASCENDING, pymongo.DESCENDING
    db[GAMES].create_index([("season", A), ("week", A)])
    db[GAMES].create_index([("home", A), ("season", A)])
    db[GAMES].create_index([("away", A), ("season", A)])
    db[PLAYERS].create_index([("name_lower", A)])
    db[PLAYERS].create_index([("latest_team", A), ("status", A)])
    db[PLAYER_WEEKS].create_index([("player_id", A), ("season", A), ("week", A)])
    db[PLAYER_WEEKS].create_index([("season", A)])
    db[PLAYER_SEASONS].create_index([("player_id", A), ("season", A)])
    db[PLAYER_SEASONS].create_index([("season", A), ("season_type", A), ("position_group", A)])
    db[PREDICTIONS].create_index([("season", A), ("week", A)])
    db[ODDS].create_index([("season", A), ("created_at", D)])
