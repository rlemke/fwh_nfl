"""Load → rate → predict → simulate against an in-memory Mongo."""

from __future__ import annotations

import datetime as dt

import mongomock
import pytest

from nfl import db as dbmod
from nfl import service, sources
from nfl.handlers.nfl_handlers import _DISPATCH, handle
from nfl.load import load
from nfl.storage import S, raw_dir

from .conftest import synthetic_games_csv

WEEK_CSV = (
    "player_id,player_display_name,position,position_group,season,week,season_type,game_id,"
    "team,opponent_team,passing_yards,passing_tds,carries,rushing_yards,def_sacks,fantasy_points_ppr\n"
    "00-1,Pat Q,QB,QB,2025,1,REG,g1,KC,BUF,300,2,3,10,,24.5\n"
    "00-1,Pat Q,QB,QB,2025,2,REG,g2,KC,DEN,250,1,2,5,,17.1\n"
    "00-2,Sam D,DE,DL,2025,1,REG,g1,OAK,SD,,,,,1.5,\n"
)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("FW_NFL_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("FW_STORAGE", "local")
    d = mongomock.MongoClient()["nfl_test"]
    monkeypatch.setattr(dbmod, "get_db", lambda client=None: d)
    S.write_text(raw_dir() + "/games.csv", synthetic_games_csv())
    S.write_text(raw_dir() + "/stats_player_week_2025.csv", WEEK_CSV)
    return d


def _paths():
    return [raw_dir() + "/games.csv", raw_dir() + "/stats_player_week_2025.csv"]


def test_load_skips_unchanged_files_and_maps_relocated_teams(db):
    first = load(db, _paths())
    assert first["loaded"]["games.csv"] == 32 * 6 * 2 // 2
    assert load(db, _paths())["skipped"] == ["games.csv", "stats_player_week_2025.csv"]
    season = db[dbmod.PLAYER_SEASONS].find_one({"player_id": "00-1"})
    assert season["games"] == 2 and season["passing_yards"] == 550
    assert db[dbmod.PLAYER_WEEKS].find_one({"player_id": "00-2"})["team"] == "LV"


def test_rate_predict_and_freeze(db):
    load(db, _paths())
    meta = service.rate_and_store(db)
    assert meta["season"] == 2025 and meta["through_game"]
    assert db[dbmod.RATINGS].count_documents({}) == 32
    g = db[dbmod.GAMES].find_one({"home_score": None})
    assert 0 < g["model"]["p_home"] < 1

    before = dt.date(2025, 9, 1)
    out = service.record_predictions(db, 2025, today=before)
    assert out["recorded"] == 16 * 3  # the unplayed weeks
    frozen = db[dbmod.PREDICTIONS].find_one({"_id": g["_id"]})

    # The game is played; a later run must settle it and NOT overwrite the prediction.
    db[dbmod.GAMES].update_one(
        {"_id": g["_id"]}, {"$set": {"home_score": 20, "away_score": 17, "model.p_home": 0.01}}
    )
    out = service.record_predictions(db, 2025, today=dt.date(2025, 12, 1))
    after = db[dbmod.PREDICTIONS].find_one({"_id": g["_id"]})
    assert after["p_home"] == frozen["p_home"] and after["margin"] == 3
    assert service.track_record(db, 2025)["model"]["n"] == 1


def test_simulation_through_the_handlers(db):
    load(db, _paths())
    service.rate_and_store(db)
    plan = handle({"_facet_name": "nfl.season.Plan", "season": 2025, "sims": 300, "chunk": 100})
    assert len(plan["chunks"]) == 3
    parts = [
        handle({"_facet_name": "nfl.season.SimulateChunk", "key": plan["key"], **c})["counts"]
        for c in plan["chunks"]
    ]
    out = handle({"_facet_name": "nfl.season.Aggregate", "key": plan["key"], "parts": parts})
    assert out["sims"] == 300 and out["favourite"]
    doc = db[dbmod.ODDS].find_one({"_id": plan["key"]})
    assert len(doc["odds"]) == 32 and set(doc["bracket"]) == {"AFC", "NFC"}
    # Planning the same state again yields the same key: chunks are reproducible.
    again = handle({"_facet_name": "nfl.season.Plan", "season": 2025, "sims": 300, "chunk": 100})
    assert again["key"] == plan["key"]


def test_every_event_facet_has_a_handler():
    import re
    from pathlib import Path

    import nfl

    ffl = (Path(nfl.__file__).parent / "ffl" / "nfl.ffl").read_text()
    declared = set()
    ns = None
    for line in ffl.splitlines():
        m = re.match(r"\s*namespace ([\w.]+)", line)
        if m:
            ns = m.group(1)
        m = re.match(r"\s*event facet (\w+)", line)
        if m:
            declared.add(f"{ns}.{m.group(1)}")
    assert declared == set(_DISPATCH)


def test_source_plan_covers_every_season():
    plan = sources.plan(2020, today=dt.date(2026, 10, 25))
    names = [s["name"] for s in plan]
    assert names[:3] == ["games.csv", "teams_colors_logos.csv", "players.csv"]
    assert names[3:] == [f"stats_player_week_{y}.csv" for y in range(2020, 2027)]
    assert sources.default_season(dt.date(2027, 1, 20)) == 2026


def test_results_are_always_revalidated():
    """A reuse window on the results file hid a Sunday's finals from every run
    inside it (2026-10-25); results and this season's stats must ask upstream."""
    age = {s["name"]: s["max_age_hours"] for s in sources.plan(2025, today=dt.date(2026, 10, 25))}
    assert age["games.csv"] == 0.0
    assert age["stats_player_week_2026.csv"] == 0.0
    assert age["stats_player_week_2025.csv"] > 0
