"""Shared fixtures: a synthetic league schedule, and the real one when reachable."""

from __future__ import annotations

import csv
import io
import random
import urllib.request
from pathlib import Path

import pytest

from nfl.teams import ALL_TEAMS

GAMES_URL = "https://github.com/nflverse/nfldata/raw/master/data/games.csv"
COLUMNS = [
    "game_id", "season", "game_type", "week", "gameday", "gametime", "away_team",
    "away_score", "home_team", "home_score", "location", "result", "away_rest",
    "home_rest", "spread_line", "home_qb_name", "away_qb_name",
]  # fmt: skip


def synthetic_games_csv(seasons=(2024, 2025), weeks=6, played_weeks_last=3, seed=7) -> str:
    """A small league: every team plays once a week; the last season is part-played."""
    rng = random.Random(seed)
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=COLUMNS)
    w.writeheader()
    for season in seasons:
        for week in range(1, weeks + 1):
            teams = list(ALL_TEAMS)
            rng.shuffle(teams)
            played = season != seasons[-1] or week <= played_weeks_last
            for i in range(0, len(teams), 2):
                home, away = teams[i], teams[i + 1]
                hs, as_ = (rng.randint(3, 38), rng.randint(3, 38)) if played else ("", "")
                if played and hs == as_:
                    hs += 3
                w.writerow(
                    {
                        "game_id": f"{season}_{week:02d}_{away}_{home}",
                        "season": season,
                        "game_type": "REG",
                        "week": week,
                        "gameday": f"{season}-09-{10 + week:02d}",
                        "gametime": "13:00",
                        "away_team": away,
                        "home_team": home,
                        "away_score": as_,
                        "home_score": hs,
                        "location": "Home",
                        "result": (hs - as_) if played else "",
                        "away_rest": 7,
                        "home_rest": 7,
                        "spread_line": rng.choice([-3, -1.5, 2.5, 7]),
                        "home_qb_name": "",
                        "away_qb_name": "",
                    }
                )
    return out.getvalue()


@pytest.fixture(scope="session")
def real_games_csv(tmp_path_factory) -> str:
    """nflverse's games.csv, downloaded once per session; skips when offline."""
    cache = Path(tmp_path_factory.getbasetemp()) / "games.csv"
    if not cache.exists():
        try:
            with urllib.request.urlopen(GAMES_URL, timeout=60) as r:
                cache.write_bytes(r.read())
        except OSError as exc:
            pytest.skip(f"nflverse unreachable: {exc}")
    return cache.read_text()


def _mongomock_bulk_compat() -> None:
    """mongomock's bulk builder predates pymongo 4.11's ``sort`` argument on
    UpdateOne/ReplaceOne and raises TypeError on it. The code under test uses
    real pymongo bulk writes, so the mock is widened rather than the code."""
    try:
        from mongomock.collection import BulkOperationBuilder
    except ImportError:
        return
    for name in ("add_update", "add_replace"):
        orig = getattr(BulkOperationBuilder, name, None)
        if orig is None or getattr(orig, "_nfl_compat", False):
            continue

        def wrapped(self, *a, _orig=orig, **kw):
            kw.pop("sort", None)
            return _orig(self, *a, **kw)

        wrapped._nfl_compat = True
        setattr(BulkOperationBuilder, name, wrapped)


_mongomock_bulk_compat()
