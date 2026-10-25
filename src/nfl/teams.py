"""Franchises, relocations and the division alignment.

nflverse keeps the abbreviation a team used AT THE TIME (``OAK`` in 2019,
``LV`` in 2020), which is right for display and wrong for anything that follows
a team across seasons: a rating that restarts at a relocation would throw away
a whole season of evidence about the same roster. Everything that tracks a team
over time keys on :func:`franchise`, the current abbreviation.

The alignment below is the one in force since the 2002 realignment, which is
also the first season the standings simulator supports. Before 2002 there were
31 teams in six divisions; those seasons still feed the ratings, which need no
divisions.
"""

from __future__ import annotations

#: Old abbreviation -> current one. ``LAR`` appears in some nflverse files as a
#: synonym for ``LA``.
RELOCATIONS: dict[str, str] = {"OAK": "LV", "SD": "LAC", "STL": "LA", "LAR": "LA"}

DIVISIONS: dict[str, list[str]] = {
    "AFC East": ["BUF", "MIA", "NE", "NYJ"],
    "AFC North": ["BAL", "CIN", "CLE", "PIT"],
    "AFC South": ["HOU", "IND", "JAX", "TEN"],
    "AFC West": ["DEN", "KC", "LV", "LAC"],
    "NFC East": ["DAL", "NYG", "PHI", "WAS"],
    "NFC North": ["CHI", "DET", "GB", "MIN"],
    "NFC South": ["ATL", "CAR", "NO", "TB"],
    "NFC West": ["ARI", "LA", "SF", "SEA"],
}

TEAM_DIVISION: dict[str, str] = {t: d for d, ts in DIVISIONS.items() for t in ts}
TEAM_CONFERENCE: dict[str, str] = {t: d.split()[0] for t, d in TEAM_DIVISION.items()}
ALL_TEAMS: list[str] = sorted(TEAM_DIVISION)

#: First season of the current alignment; the simulator refuses anything older.
FIRST_ALIGNED_SEASON = 2002


def franchise(abbr: str) -> str:
    """The franchise's current abbreviation."""
    return RELOCATIONS.get(abbr, abbr)


def division_of(team: str) -> str:
    return TEAM_DIVISION[franchise(team)]


def conference_of(team: str) -> str:
    return TEAM_CONFERENCE[franchise(team)]


def playoff_teams_per_conference(season: int) -> int:
    """Six per conference through 2019, seven since (one first-round bye)."""
    return 7 if season >= 2020 else 6
