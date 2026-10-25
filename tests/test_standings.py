"""Tiebreakers and seeding — synthetic cases, then every real postseason since 2002."""

from __future__ import annotations

import random

import pytest

from nfl.games import parse_games_csv
from nfl.standings import DIVISION_STEPS, WILDCARD_STEPS, Standings, _pick, seed_conference
from nfl.teams import conference_of, playoff_teams_per_conference

RNG = random.Random(0)


def test_division_head_to_head_decides_first():
    st = Standings([
        ("BUF", "MIA", 7), ("MIA", "BUF", -3),  # BUF swept MIA
        ("KC", "BUF", 3), ("DEN", "BUF", 3),
        ("MIA", "KC", 3), ("MIA", "DEN", 3),
    ])  # fmt: skip
    assert st.pct("BUF") == st.pct("MIA")
    assert _pick(st, ["BUF", "MIA"], DIVISION_STEPS, RNG) == "BUF"


def test_division_record_breaks_a_split_series():
    st = Standings([
        ("BUF", "MIA", 7), ("MIA", "BUF", 7),  # split
        ("BUF", "NYJ", 3), ("NE", "MIA", 3),  # BUF 2-1 in division, MIA 1-2
        ("KC", "BUF", 3), ("DEN", "BUF", 3),
        ("MIA", "DEN", 3), ("KC", "MIA", 3),
    ])  # fmt: skip
    assert st.pct("BUF") == st.pct("MIA")
    assert _pick(st, ["BUF", "MIA"], DIVISION_STEPS, RNG) == "BUF"


def test_wildcard_pair_that_never_met_goes_to_conference_record():
    st = Standings([("DEN", "NYG", 3), ("KC", "DEN", 3), ("PIT", "BAL", 3), ("DAL", "PIT", 3)])
    assert _pick(st, ["DEN", "PIT"], WILDCARD_STEPS, RNG) == "PIT"


def test_wildcard_three_way_sweep_wins():
    st = Standings([("DEN", "PIT", 3), ("IND", "DEN", -3), ("PIT", "IND", 3)])
    assert _pick(st, ["DEN", "PIT", "IND"], WILDCARD_STEPS, RNG) == "DEN"


def test_wildcard_three_way_without_a_sweep_skips_head_to_head():
    # DEN beat PIT, PIT beat IND, IND beat DEN: nobody swept; conference record decides.
    st = Standings([("DEN", "PIT", 3), ("PIT", "IND", 3), ("IND", "DEN", 3), ("IND", "BAL", 3)])
    assert _pick(st, ["DEN", "PIT", "IND"], WILDCARD_STEPS, RNG) == "IND"


def test_a_coin_toss_is_the_last_resort():
    st = Standings([])
    picks = {_pick(st, ["DEN", "PIT"], WILDCARD_STEPS, random.Random(s)) for s in range(20)}
    assert picks == {"DEN", "PIT"}


def _actual_vs_ours(real_games_csv):
    games = parse_games_csv(real_games_csv)
    bad = []
    for season in range(2002, max(g.season for g in games if g.game_type == "SB" and g.played) + 1):
        st = Standings(
            [
                (g.home, g.away, g.margin)
                for g in games
                if g.season == season and g.game_type == "REG" and g.played
            ]
        )
        post = [g for g in games if g.season == season and g.postseason]
        for conf in ("AFC", "NFC"):
            seeds, _ = seed_conference(st, conf, season, random.Random(1))
            n = playoff_teams_per_conference(season)
            wc = [g for g in post if g.game_type == "WC" and conference_of(g.home) == conf]
            later = [
                g for g in post if g.game_type in ("DIV", "CON") and conference_of(g.home) == conf
            ]
            in_wc = {t for g in wc for t in (g.home, g.away)}
            byes = {t for g in later if g.game_type == "DIV" for t in (g.home, g.away)} - in_wc
            if n == 7:
                want_pairs = {(seeds[1], seeds[6]), (seeds[2], seeds[5]), (seeds[3], seeds[4])}
            else:
                want_pairs = {(seeds[2], seeds[5]), (seeds[3], seeds[4])}
            rank = {t: i for i, t in enumerate(seeds)}
            hosts_ok = all(rank[g.home] < rank[g.away] for g in later)
            if (
                set(seeds) != in_wc | byes
                or {(g.home, g.away) for g in wc} != want_pairs
                or byes != set(seeds[: 1 if n == 7 else 2])
                or not hosts_ok
            ):
                bad.append((season, conf, seeds))
    return bad


def test_seeding_reproduces_every_real_postseason_since_2002(real_games_csv):
    """Who got in, the byes, every wild-card pairing with its host, and that the
    higher seed hosted every later conference game. Measured 2026-10-25: 48/48."""
    assert _actual_vs_ours(real_games_csv) == []


@pytest.mark.parametrize("season,n", [(2019, 6), (2020, 7)])
def test_playoff_field_size(season, n):
    assert playoff_teams_per_conference(season) == n
