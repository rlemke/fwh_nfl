"""The game record every model module works on, parsed from nflverse ``games.csv``.

One parser, so the ratings, the backtest, the simulator and the loader cannot
disagree about what a row means. Two conventions in the source are easy to get
backwards and are fixed here:

- ``result`` and ``spread_line`` are both from the HOME team's side: a
  ``spread_line`` of 3 means the home team was favoured by 3, and a ``result``
  of 3 means it won by 3.
- An unplayed game has EMPTY scores, not zeros. A 0–0 game would be a tie.
"""

from __future__ import annotations

import csv
import io
import math
from collections.abc import Iterable
from dataclasses import dataclass

from .teams import franchise

POSTSEASON = ("WC", "DIV", "CON", "SB")


@dataclass(frozen=True)
class Game:
    game_id: str
    season: int
    game_type: str  # REG | WC | DIV | CON | SB
    week: int
    gameday: str  # YYYY-MM-DD
    gametime: str  # HH:MM, local to the venue
    home: str  # franchise abbreviation
    away: str
    home_score: int | None
    away_score: int | None
    neutral: bool
    home_rest: int | None
    away_rest: int | None
    spread_line: float | None  # home side: + means home favoured
    home_abbr: str = ""  # abbreviation as of that season (OAK, SD, ...)
    away_abbr: str = ""
    home_qb: str = ""
    away_qb: str = ""

    @property
    def played(self) -> bool:
        return self.home_score is not None and self.away_score is not None

    @property
    def margin(self) -> int | None:
        """Home score minus away score."""
        if not self.played:
            return None
        return self.home_score - self.away_score  # type: ignore[operator]

    @property
    def postseason(self) -> bool:
        return self.game_type in POSTSEASON

    @property
    def sort_key(self) -> tuple:
        return (self.season, self.gameday, self.gametime, self.game_id)


def _int(value: str) -> int | None:
    value = (value or "").strip()
    if not value or value.upper() == "NA":
        return None
    return int(float(value))


def _float(value: str) -> float | None:
    value = (value or "").strip()
    if not value or value.upper() == "NA":
        return None
    out = float(value)
    return None if math.isnan(out) else out


def parse_games(rows: Iterable[dict]) -> list[Game]:
    """Parse ``games.csv`` rows, sorted in the order they were played."""
    games = []
    for r in rows:
        games.append(
            Game(
                game_id=r["game_id"],
                season=int(r["season"]),
                game_type=r["game_type"],
                week=int(r["week"]),
                gameday=r.get("gameday") or "",
                gametime=r.get("gametime") or "",
                home=franchise(r["home_team"]),
                away=franchise(r["away_team"]),
                home_score=_int(r.get("home_score", "")),
                away_score=_int(r.get("away_score", "")),
                neutral=(r.get("location") or "").strip() == "Neutral",
                home_rest=_int(r.get("home_rest", "")),
                away_rest=_int(r.get("away_rest", "")),
                spread_line=_float(r.get("spread_line", "")),
                home_abbr=r["home_team"],
                away_abbr=r["away_team"],
                home_qb=r.get("home_qb_name") or "",
                away_qb=r.get("away_qb_name") or "",
            )
        )
    games.sort(key=lambda g: g.sort_key)
    return games


def parse_games_csv(text: str) -> list[Game]:
    return parse_games(csv.DictReader(io.StringIO(text)))


def current_season(games: list[Game]) -> int:
    """The latest season that has a schedule."""
    return max(g.season for g in games)
