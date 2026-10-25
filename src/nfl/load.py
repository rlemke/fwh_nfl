"""Load fetched nflverse files into MongoDB.

Each file is loaded only when its content changed: the SHA-256 that
``fw.http.Fetch`` recorded in the file's sidecar is compared with the one
stored at the last successful load. A nightly run therefore re-reads the
current season and skips 27 seasons nobody has touched.

Loads are idempotent per unit: a season's player rows are deleted and
re-inserted together, and the file's checksum is recorded only AFTER that
succeeds. A crash halfway leaves the checksum unrecorded, so the next run
simply does the season again.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime

import pymongo

from . import db as dbmod
from .games import parse_games
from .storage import S
from .teams import franchise

Log = Callable[..., None] | None

_WEEK_FILE = re.compile(r"stats_player_week_(\d{4})\.csv$")

#: Integer stat columns kept per player-game (nflverse ships ~150).
INT_STATS = [
    "completions", "attempts", "passing_yards", "passing_tds", "passing_interceptions",
    "sacks_suffered", "carries", "rushing_yards", "rushing_tds", "receptions", "targets",
    "receiving_yards", "receiving_tds", "def_tackles_solo", "def_tackle_assists",
    "def_tackles_for_loss", "def_interceptions", "def_pass_defended", "def_fumbles_forced",
    "def_tds", "fumbles_lost_total", "fg_made", "fg_att", "pat_made", "pat_att",
    "punt_returns", "punt_return_yards", "kickoff_returns", "kickoff_return_yards",
    "special_teams_tds",
]  # fmt: skip
FLOAT_STATS = ["def_sacks", "passing_epa", "rushing_epa", "receiving_epa", "fantasy_points_ppr"]


def _num(value: str, kind=int):
    value = (value or "").strip()
    if not value or value.upper() == "NA":
        return None
    try:
        return kind(float(value)) if kind is int else round(float(value), 3)
    except ValueError:
        return None


def _sha(path: str) -> str:
    """The checksum ``fw.http.Fetch`` recorded; hashed here if there is no sidecar."""
    side = path + ".meta.json"
    if S.exists(side):
        try:
            sha = json.loads(S.read_text(side)).get("sha256")
            if sha:
                return sha
        except ValueError:
            pass
    return hashlib.sha256(S.read_bytes(path)).hexdigest()


def _rows(path: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(S.read_text(path))))


def load_games(db, path: str) -> int:
    games = parse_games(_rows(path))
    ops = []
    for g in games:
        doc = {
            "_id": g.game_id,
            "season": g.season,
            "game_type": g.game_type,
            "week": g.week,
            "gameday": g.gameday,
            "gametime": g.gametime,
            "home": g.home,
            "away": g.away,
            "home_abbr": g.home_abbr,
            "away_abbr": g.away_abbr,
            "home_score": g.home_score,
            "away_score": g.away_score,
            "neutral": g.neutral,
            "home_rest": g.home_rest,
            "away_rest": g.away_rest,
            "spread_line": g.spread_line,
            "home_qb": g.home_qb,
            "away_qb": g.away_qb,
        }
        # $set, not replace: the ratings step adds model fields to these docs.
        ops.append(pymongo.UpdateOne({"_id": g.game_id}, {"$set": doc}, upsert=True))
    if ops:
        db[dbmod.GAMES].bulk_write(ops, ordered=False)
    return len(games)


def load_teams(db, path: str) -> int:
    n = 0
    for r in _rows(path):
        abbr = r["team_abbr"]
        if franchise(abbr) != abbr:
            continue  # relocated names: the franchise document is under the current one
        db[dbmod.TEAMS].replace_one(
            {"_id": abbr},
            {
                "_id": abbr,
                "name": r["team_name"],
                "nick": r["team_nick"],
                "conference": r["team_conf"],
                "division": r["team_division"],
                "color": r["team_color"],
                "color2": r["team_color2"],
                "logo": r["team_logo_espn"],
            },
            upsert=True,
        )
        n += 1
    return n


def load_players(db, path: str) -> int:
    ops = []
    for r in _rows(path):
        pid = r.get("gsis_id")
        if not pid:
            continue
        name = r.get("display_name") or ""
        ops.append(
            pymongo.ReplaceOne(
                {"_id": pid},
                {
                    "_id": pid,
                    "name": name,
                    "name_lower": name.lower(),
                    "position": r.get("position"),
                    "position_group": r.get("position_group"),
                    "birth_date": r.get("birth_date"),
                    "height": _num(r.get("height", "")),
                    "weight": _num(r.get("weight", "")),
                    "college": r.get("college_name"),
                    "jersey_number": _num(r.get("jersey_number", "")),
                    "rookie_season": _num(r.get("rookie_season", "")),
                    "last_season": _num(r.get("last_season", "")),
                    "latest_team": franchise(r["latest_team"]) if r.get("latest_team") else None,
                    "status": r.get("status"),
                    "headshot": r.get("headshot"),
                    "draft_year": _num(r.get("draft_year", "")),
                    "draft_round": _num(r.get("draft_round", "")),
                    "draft_pick": _num(r.get("draft_pick", "")),
                    "draft_team": franchise(r["draft_team"]) if r.get("draft_team") else None,
                },
                upsert=True,
            )
        )
        if len(ops) >= 2000:
            db[dbmod.PLAYERS].bulk_write(ops, ordered=False)
            ops = []
    if ops:
        db[dbmod.PLAYERS].bulk_write(ops, ordered=False)
    return db[dbmod.PLAYERS].estimated_document_count()


def _week_doc(r: dict) -> dict:
    doc = {
        "player_id": r["player_id"],
        "name": r.get("player_display_name") or r.get("player_name"),
        "position": r.get("position"),
        "position_group": r.get("position_group"),
        "season": int(r["season"]),
        "week": int(r["week"]),
        "season_type": r.get("season_type"),
        "game_id": r.get("game_id"),
        "team": franchise(r.get("team") or ""),
        "opponent": franchise(r.get("opponent_team") or ""),
    }
    for c in INT_STATS:
        v = _num(r.get(c, ""))
        if v:
            doc[c] = v
    for c in FLOAT_STATS:
        v = _num(r.get(c, ""), float)
        if v:
            doc[c] = v
    return doc


def season_totals(weeks: list[dict]) -> list[dict]:
    """Sum player-games into one row per player per season type."""
    out: dict[tuple, dict] = {}
    for w in weeks:
        key = (w["player_id"], w["season"], w["season_type"])
        s = out.get(key)
        if s is None:
            s = out[key] = {
                "player_id": w["player_id"],
                "name": w["name"],
                "position": w["position"],
                "position_group": w["position_group"],
                "season": w["season"],
                "season_type": w["season_type"],
                "teams": [],
                "games": 0,
            }
        s["games"] += 1
        if w["team"] and w["team"] not in s["teams"]:
            s["teams"].append(w["team"])
        for c in INT_STATS + FLOAT_STATS:
            if c in w:
                s[c] = round(s.get(c, 0) + w[c], 3)
    return list(out.values())


def load_player_weeks(db, path: str, season: int) -> int:
    weeks = [_week_doc(r) for r in _rows(path) if r.get("player_id")]
    # Replace the season as a unit; the checksum is recorded only after this.
    db[dbmod.PLAYER_WEEKS].delete_many({"season": season})
    db[dbmod.PLAYER_SEASONS].delete_many({"season": season})
    if weeks:
        db[dbmod.PLAYER_WEEKS].insert_many(weeks, ordered=False)
        db[dbmod.PLAYER_SEASONS].insert_many(season_totals(weeks), ordered=False)
    return len(weeks)


def _loaded_files(db) -> dict[str, dict]:
    """{file name: {sha256, rows, loaded_at}} as of the last successful loads."""
    doc = db[dbmod.META].find_one({"_id": "sources"}) or {}
    return {v["name"]: v for v in (doc.get("files") or {}).values()}


def _record(db, name: str, info: dict) -> None:
    # Mongo field names cannot contain '.', and every file name does.
    key = name.replace(".", "_")
    db[dbmod.META].update_one(
        {"_id": "sources"}, {"$set": {f"files.{key}": {**info, "name": name}}}, upsert=True
    )


def load(db, paths: list[str], force: bool = False, log: Log = None) -> dict:
    """Load every path whose content changed; returns per-file outcomes."""
    dbmod.ensure_indexes(db)
    known = _loaded_files(db)
    loaded, skipped = {}, []
    for path in paths:
        name = path.rsplit("/", 1)[-1]
        sha = _sha(path)
        if not force and known.get(name, {}).get("sha256") == sha:
            skipped.append(name)
            continue
        m = _WEEK_FILE.search(name)
        if name == "games.csv":
            n = load_games(db, path)
        elif name == "teams_colors_logos.csv":
            n = load_teams(db, path)
        elif name == "players.csv":
            n = load_players(db, path)
        elif m:
            n = load_player_weeks(db, path, int(m.group(1)))
        else:
            raise ValueError(f"don't know how to load {name!r}")
        # Recorded per file, AFTER its load: a failure later in the list keeps
        # this file's progress, and a failure in this one leaves it to redo.
        _record(
            db,
            name,
            {
                "sha256": sha,
                "rows": n,
                "loaded_at": datetime.now(UTC).isoformat(timespec="seconds"),
            },
        )
        loaded[name] = n
        if log:
            log(f"Loaded {name}: {n:,} rows", level="info")
    return {"loaded": loaded, "skipped": skipped}
