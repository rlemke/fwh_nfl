"""NFL domain — players, teams, game predictions and the path to the Super Bowl.

Data comes from nflverse (schedules and results since 1999, weekly player
stats, rosters), fetched with the built-in ``fw.http`` facets so a nightly
refresh only re-downloads what changed upstream.

The model is an Elo team rating. It is scored, game by game, against the
betting market's closing line on the same games — a prediction shown without
its track record is not worth showing.

Discovered by the Facetwork runner via the ``facetwork.domains`` entry point::

    [project.entry-points."facetwork.domains"]
    nfl = "nfl:domain"
"""

from __future__ import annotations

from pathlib import Path

from facetwork.domains import DomainPackage

from .handlers import register_all_registry_handlers

domain = DomainPackage(
    name="nfl",
    ffl_dir=Path(__file__).parent / "ffl",
    register_handlers=register_all_registry_handlers,
)
