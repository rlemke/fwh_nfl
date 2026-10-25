"""The nflverse files this domain reads, and how long each may be reused.

⚠️ nflverse renamed its player-stats release in 2025 (``player_stats/`` ->
``stats_player/stats_player_week_<season>.csv``); the old path now 404s. The
URLs below are the current ones, checked 2026-10-25.

Freshness is set per file because the files move at different rates: the
current season's schedule and stats change nightly in season, a finished
season's stats essentially never. ``fw.http.Fetch`` still revalidates a stale
file with a conditional GET, so a long reuse window costs nothing when the file
has not changed.
"""

from __future__ import annotations

import datetime as _dt

from .storage import join, raw_dir

NFLDATA = "https://github.com/nflverse/nfldata/raw/master/data"
RELEASES = "https://github.com/nflverse/nflverse-data/releases/download"

FIRST_SEASON = 1999  # the first season nflverse covers play by play


def default_season(today: _dt.date | None = None) -> int:
    """The NFL season in progress (or just finished): it starts in September (a season file 404s before then)."""
    today = today or _dt.date.today()
    return today.year if today.month >= 9 else today.year - 1


def plan(
    first_season: int = FIRST_SEASON, last_season: int = 0, today: _dt.date | None = None
) -> list[dict]:
    """One entry per file: ``{name, url, dest, max_age_hours}``."""
    current = last_season or default_season(today)
    first = max(first_season or FIRST_SEASON, FIRST_SEASON)
    base = raw_dir()
    out = [
        {"name": "games.csv", "url": f"{NFLDATA}/games.csv", "max_age_hours": 6.0},
        {
            "name": "teams_colors_logos.csv",
            "url": f"{RELEASES}/teams/teams_colors_logos.csv",
            "max_age_hours": 24.0 * 30,
        },
        {"name": "players.csv", "url": f"{RELEASES}/players/players.csv", "max_age_hours": 24.0},
    ]
    for season in range(first, current + 1):
        out.append(
            {
                "name": f"stats_player_week_{season}.csv",
                "url": f"{RELEASES}/stats_player/stats_player_week_{season}.csv",
                "max_age_hours": 12.0 if season >= current - 1 else 24.0 * 90,
            }
        )
    for s in out:
        s["dest"] = join(base, s["name"])
    return out
