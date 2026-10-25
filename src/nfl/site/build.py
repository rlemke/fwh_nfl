"""Render the NFL database as a static website.

The dashboard app at ``/nfl`` needs a server and the database behind it; this
renders the same views to plain HTML so they can be published (GitHub Pages)
and read by anyone. Every page is self-contained — styles inline, data inline,
no external requests — so the bundle can be published HTML-only.

Layout (relative links throughout, so it works under any URL prefix)::

    index.html              this season: games by week, odds, projected bracket
    model.html              how good the model is: backtest, calibration, live record
    teams/<ABBR>.html       one page per team
    players/index.html      season leaders + search over every player with stats
    players/sNN.html        player detail, SHARDS of the player set rendered by
                            hash — 11k players as 11k files would dominate the
                            site, and a single file would be too big to load

⚠️ The output is deterministic: no build timestamp, sorted iteration, rounded
floats. A day with no new data then renders byte-identical pages, so the
publisher's commit is empty and the Pages history does not grow by a copy of
the site every day. The simulation is seeded from the season state for the
same reason.

The view logic mirrors ``facetwork/dashboard/routes/domain/nfl.py``. It is
duplicated, not shared, because the dashboard image does not install domain
packages; change both when a view changes.
"""

from __future__ import annotations

import json
import os
import zlib
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

ROUND_NAMES = {"WC": "Wild card", "DIV": "Divisional", "CON": "Conference", "SB": "Super Bowl"}
ROUND_ORDER = {"WC": 1, "DIV": 2, "CON": 3, "SB": 4}
POSITION_ORDER = ["QB", "RB", "WR", "TE", "OL", "DL", "LB", "DB", "SPEC"]
SHARDS = 64
FFL_URL = "https://github.com/rlemke/fwh_nfl/blob/main/src/nfl/ffl/nfl_site.ffl"
REPO_URL = "https://github.com/rlemke/fwh_nfl"

LEADER_STATS = [
    ("Passing yards", "passing_yards"),
    ("Passing TDs", "passing_tds"),
    ("Rushing yards", "rushing_yards"),
    ("Receiving yards", "receiving_yards"),
    ("Receptions", "receptions"),
    ("Sacks", "def_sacks"),
    ("Interceptions", "def_interceptions"),
    ("Tackles (solo)", "def_tackles_solo"),
]

STAT_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "QB": [
        ("Cmp", "completions"), ("Att", "attempts"), ("Pass yds", "passing_yards"),
        ("TD", "passing_tds"), ("Int", "passing_interceptions"), ("Sacked", "sacks_suffered"),
        ("Pass EPA", "passing_epa"), ("Rush yds", "rushing_yards"), ("Rush TD", "rushing_tds"),
    ],
    "RB": [
        ("Car", "carries"), ("Rush yds", "rushing_yards"), ("Rush TD", "rushing_tds"),
        ("Rec", "receptions"), ("Rec yds", "receiving_yards"), ("Rec TD", "receiving_tds"),
        ("Rush EPA", "rushing_epa"), ("Fum lost", "fumbles_lost_total"),
    ],
    "WR": [
        ("Tgt", "targets"), ("Rec", "receptions"), ("Rec yds", "receiving_yards"),
        ("Rec TD", "receiving_tds"), ("Rec EPA", "receiving_epa"), ("Rush yds", "rushing_yards"),
    ],
    "DEF": [
        ("Solo", "def_tackles_solo"), ("Ast", "def_tackle_assists"), ("TFL", "def_tackles_for_loss"),
        ("Sacks", "def_sacks"), ("Int", "def_interceptions"), ("PD", "def_pass_defended"),
        ("FF", "def_fumbles_forced"), ("TD", "def_tds"),
    ],
    "SPEC": [
        ("FG", "fg_made"), ("FGA", "fg_att"), ("XP", "pat_made"), ("XPA", "pat_att"),
        ("PR yds", "punt_return_yards"), ("KR yds", "kickoff_return_yards"),
    ],
}  # fmt: skip
STAT_COLUMNS["TE"] = STAT_COLUMNS["WR"]
for _g in ("DL", "LB", "DB"):
    STAT_COLUMNS[_g] = STAT_COLUMNS["DEF"]


def shard_of(player_id: str) -> int:
    """Stable shard for a player (crc32, so it does not vary by process)."""
    return zlib.crc32(player_id.encode()) % SHARDS


def _shard_href(player_id: str) -> str:
    return f"s{shard_of(player_id):02d}.html#{player_id}"


# --------------------------------------------------------------------------
# views (mirror the dashboard's)
# --------------------------------------------------------------------------


def _season_now(db) -> int:
    meta = db.nfl_meta.find_one({"_id": "ratings"}) or {}
    if meta.get("season"):
        return int(meta["season"])
    latest = db.nfl_games.find_one(sort=[("season", -1)])
    return int(latest["season"]) if latest else 0


def _record(games: list[dict], team: str) -> dict:
    w = lost = t = 0
    for g in games:
        if g.get("game_type") != "REG" or g.get("home_score") is None:
            continue
        m = g["home_score"] - g["away_score"]
        if g["away"] == team:
            m = -m
        elif g["home"] != team:
            continue
        if m > 0:
            w += 1
        elif m < 0:
            lost += 1
        else:
            t += 1
    return {"w": w, "l": lost, "t": t, "text": f"{w}-{lost}" + (f"-{t}" if t else "")}


def _game_view(g: dict) -> dict:
    m = g.get("model") or {}
    played = g.get("home_score") is not None
    p_home = m.get("p_home")
    out = {
        "week": g["week"],
        "game_type": g["game_type"],
        "round": ROUND_NAMES.get(g["game_type"], f"Week {g['week']}"),
        "gameday": g.get("gameday") or "",
        "gametime": g.get("gametime") or "",
        "home": g["home"],
        "away": g["away"],
        "neutral": g.get("neutral"),
        "played": played,
        "home_score": g.get("home_score"),
        "away_score": g.get("away_score"),
        "p_home": p_home,
        "spread_pred": m.get("spread_pred"),
        "spread_line": g.get("spread_line"),
        "home_qb": g.get("home_qb") or "",
        "away_qb": g.get("away_qb") or "",
    }
    if p_home is not None:
        out["pick"] = g["home"] if p_home >= 0.5 else g["away"]
        out["pick_p"] = max(p_home, 1 - p_home)
    if played and p_home is not None:
        margin = g["home_score"] - g["away_score"]
        out["pick_right"] = None if margin == 0 else (margin > 0) == (p_home >= 0.5)
    return out


def _points(series: list[float], width: int, height: int, pad: int = 6) -> dict:
    if len(series) < 2:
        return {"points": ""}
    lo, hi = min(series), max(series)
    span = (hi - lo) or 1.0
    step = (width - 2 * pad) / (len(series) - 1)
    pts = " ".join(
        f"{pad + i * step:.1f},{pad + (height - 2 * pad) * (1 - (v - lo) / span):.1f}"
        for i, v in enumerate(series)
    )
    return {"points": pts, "min": round(lo), "max": round(hi), "width": width, "height": height}


def _season_history(games: list[dict], rating: dict, team: str) -> list[dict]:
    by_season: dict[int, list[dict]] = defaultdict(list)
    for g in games:
        by_season[g["season"]].append(g)
    final_elo = {season: elo for season, _w, _gt, elo in rating.get("history") or []}
    rows = []
    for season in sorted(by_season, reverse=True):
        sg = by_season[season]
        pf = pa = 0
        furthest, won_sb, abbr = None, False, team
        for g in sg:
            if g.get("home_score") is None:
                continue
            mine_home = g["home"] == team
            abbr = g.get("home_abbr" if mine_home else "away_abbr") or team
            if g["game_type"] == "REG":
                pf += g["home_score"] if mine_home else g["away_score"]
                pa += g["away_score"] if mine_home else g["home_score"]
            elif g["game_type"] in ROUND_ORDER:
                if furthest is None or ROUND_ORDER[g["game_type"]] > ROUND_ORDER[furthest]:
                    furthest = g["game_type"]
                margin = g["home_score"] - g["away_score"]
                if g["game_type"] == "SB" and margin != 0 and (margin > 0) == mine_home:
                    won_sb = True
        if won_sb:
            playoff = "Won Super Bowl"
        elif furthest:
            playoff = f"Lost {ROUND_NAMES[furthest].lower()}" + (
                "" if furthest == "SB" else " round"
            )
        else:
            playoff = ""
        rows.append(
            {
                "season": season,
                "abbr": abbr,
                "record": _record(sg, team)["text"],
                "pf": pf,
                "pa": pa,
                "playoff": playoff,
                "champion": won_sb,
                "elo": final_elo.get(season),
            }
        )
    return rows


def _live_record(db, season: int) -> dict:
    n = right = mkt_n = mkt_right = brier = mkt_brier = 0.0
    for p in db.nfl_predictions.find({"season": season, "margin": {"$exists": True}}):
        y = 1.0 if p["margin"] > 0 else 0.0 if p["margin"] < 0 else 0.5
        n += 1
        right += 0.5 if y == 0.5 else float((p["p_home"] >= 0.5) == (y == 1.0))
        brier += (p["p_home"] - y) ** 2
        if p.get("p_home_market") is not None:
            mkt_n += 1
            mkt_right += 0.5 if y == 0.5 else float((p["p_home_market"] >= 0.5) == (y == 1.0))
            mkt_brier += (p["p_home_market"] - y) ** 2
    first = db.nfl_predictions.find_one(
        {"season": season}, {"predicted_at": 1}, sort=[("predicted_at", 1)]
    )
    return {
        "since": ((first or {}).get("predicted_at") or "")[:10],
        "n": int(n),
        "accuracy": right / n if n else None,
        "brier": brier / n if n else None,
        "market_n": int(mkt_n),
        "market_accuracy": mkt_right / mkt_n if mkt_n else None,
    }


def _round(v: Any) -> Any:
    """Compact numbers for inlined JSON: ints stay ints, floats get 2 decimals."""
    if isinstance(v, float):
        r = round(v, 2)
        return int(r) if r == int(r) else r
    return v


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(Path(__file__).parent / "templates")),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    env.filters["tojson_compact"] = lambda v: (
        json.dumps(v, separators=(",", ":"), sort_keys=True)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    return env


def build_site(db, out_dir: str, log: Callable[..., None] | None = None) -> dict:
    """Write the whole site under ``out_dir``; return a file count and bytes."""
    env = _env()
    out = Path(out_dir)
    (out / "teams").mkdir(parents=True, exist_ok=True)
    (out / "players").mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    def write(rel: str, template: str, **ctx: Any) -> None:
        path = out / rel
        depth = rel.count("/")
        html = env.get_template(template).render(
            root="../" * depth, ffl_url=FFL_URL, repo_url=REPO_URL, **ctx
        )
        path.write_text(html, encoding="utf-8")
        written.append(path)

    teams = {t["_id"]: t for t in db.nfl_teams.find().sort("_id", 1)}
    if not teams:
        raise RuntimeError("no NFL teams loaded — run nfl.workflows.Update first")
    season = _season_now(db)
    meta = db.nfl_meta.find_one({"_id": "ratings"}) or {}
    meta.pop("computed_at", None)  # a run time, not a data time: keeps output stable
    backtest = db.nfl_meta.find_one({"_id": "backtest"})
    live = _live_record(db, season)
    odds = db.nfl_odds.find_one({"season": season}, sort=[("created_at", -1)])
    ratings = {r["_id"]: r for r in db.nfl_team_ratings.find()}
    season_games = list(
        db.nfl_games.find({"season": season}).sort([("gameday", 1), ("gametime", 1), ("_id", 1)])
    )
    common = {"season": season, "teams": teams, "meta": meta, "backtest": backtest, "live": live}

    # ---- index ---------------------------------------------------------
    week_keys = sorted({g["week"] for g in season_games})
    upcoming = next((g["week"] for g in season_games if g.get("home_score") is None), None)
    shown_week = upcoming or (week_keys[-1] if week_keys else 1)
    weeks = [
        {
            "week": w,
            "label": next(
                (ROUND_NAMES[g["game_type"]] for g in season_games
                 if g["week"] == w and g["game_type"] in ROUND_NAMES),
                f"Week {w}",
            ),
            "games": [_game_view(g) for g in season_games if g["week"] == w],
        }
        for w in week_keys
    ]  # fmt: skip
    standings = []
    for row in (odds or {}).get("odds", []):
        t = row["team"]
        standings.append(
            {
                **row,
                "record": _record(season_games, t)["text"],
                "elo": ratings.get(t, {}).get("elo"),
                "rank": ratings.get(t, {}).get("rank"),
                "division_name": teams.get(t, {}).get("division", ""),
            }
        )
    by_conf = {
        c: sorted(
            (r for r in standings if r["conference"] == c),
            key=lambda r: (-r["playoffs"], r["team"]),
        )
        for c in ("AFC", "NFC")
    }
    write(
        "index.html",
        "index.html",
        **common,
        weeks=weeks,
        shown_week=shown_week,
        odds=odds,
        by_conf=by_conf,
        postseason=[_game_view(g) for g in season_games if g["game_type"] in ROUND_ORDER],
    )
    write("model.html", "model.html", **common)

    # ---- teams ---------------------------------------------------------
    all_games: dict[str, list[dict]] = defaultdict(list)
    for g in db.nfl_games.find(
        {},
        {"season": 1, "game_type": 1, "home": 1, "away": 1, "home_score": 1,
         "away_score": 1, "home_abbr": 1, "away_abbr": 1},
    ).sort("_id", 1):  # fmt: skip
        all_games[g["home"]].append(g)
        all_games[g["away"]].append(g)
    for abbr, team in teams.items():
        rating = ratings.get(abbr, {})
        history = rating.get("history") or []
        chart = _points([h[3] for h in history], width=760, height=170)
        marks, prev = [], None
        for i, h in enumerate(history):
            if h[0] != prev and h[0] % 5 == 0:
                marks.append({"x": round(6 + i * (760 - 12) / max(len(history) - 1, 1), 1),
                              "label": h[0]})  # fmt: skip
            prev = h[0]
        games = [g for g in season_games if abbr in (g["home"], g["away"])]
        roster_by_pos: dict[str, list[dict]] = defaultdict(list)
        for p in db.nfl_players.find({"latest_team": abbr, "status": "ACT"}).sort(
            [("name", 1), ("_id", 1)]
        ):
            roster_by_pos[p.get("position_group") or "Other"].append(
                {**p, "href": "../players/" + _shard_href(p["_id"])}
            )
        roster = [(g, roster_by_pos[g]) for g in POSITION_ORDER if roster_by_pos.get(g)]
        roster += sorted((g, ps) for g, ps in roster_by_pos.items() if g not in POSITION_ORDER)
        leaders = []
        for label, field, groups in (
            ("Passing yards", "passing_yards", ["QB"]),
            ("Rushing yards", "rushing_yards", None),
            ("Receiving yards", "receiving_yards", None),
            ("Sacks", "def_sacks", None),
            ("Tackles (solo)", "def_tackles_solo", None),
        ):
            q: dict[str, Any] = {"season": season, "season_type": "REG", "teams": abbr,
                                 field: {"$gt": 0}}  # fmt: skip
            if groups:
                q["position_group"] = {"$in": groups}
            rows = [
                {**r, "href": "../players/" + _shard_href(r["player_id"])}
                for r in db.nfl_player_seasons.find(q)
                .sort([(field, -1), ("player_id", 1)])
                .limit(3)
            ]
            if rows:
                leaders.append({"label": label, "field": field, "rows": rows})
        team_odds = next((r for r in standings if r["team"] == abbr), None)
        write(
            f"teams/{abbr}.html",
            "team.html",
            **common,
            team=team,
            abbr=abbr,
            rating=rating,
            chart=chart,
            season_marks=marks,
            games=[_game_view(g) for g in games],
            record=_record(games, abbr),
            team_odds=team_odds,
            odds=odds,
            roster=roster,
            leaders=leaders,
            history=_season_history(all_games[abbr], rating, abbr),
        )

    # ---- players -------------------------------------------------------
    players = {p["_id"]: p for p in db.nfl_players.find()}
    seasons_by_player: dict[str, list[dict]] = defaultdict(list)
    for s in db.nfl_player_seasons.find({}, {"_id": 0}).sort(
        [("player_id", 1), ("season", -1), ("season_type", 1)]
    ):
        seasons_by_player[s["player_id"]].append(s)
    log_rows: dict[str, list[dict]] = defaultdict(list)
    for w in db.nfl_player_weeks.find({"season": season}, {"_id": 0}).sort(
        [("player_id", 1), ("week", 1)]
    ):
        log_rows[w["player_id"]].append(w)

    index_rows = []
    shards: dict[int, dict[str, Any]] = defaultdict(dict)
    for pid in sorted(seasons_by_player):
        rows = seasons_by_player[pid]
        p = players.get(pid) or {}
        group = p.get("position_group") or rows[0].get("position_group") or ""
        cols = STAT_COLUMNS.get(group, STAT_COLUMNS["WR"])
        fields = [f for _h, f in cols]

        def pack(r: dict, fields: list[str] = fields) -> list:
            return [r["season"], "/".join(r.get("teams") or []), r.get("games", 0)] + [
                _round(r.get(f, 0) or 0) for f in fields
            ]

        years = [r["season"] for r in rows]
        entry = {
            "name": p.get("name") or rows[0]["name"],
            "pos": p.get("position") or rows[0].get("position") or "",
            "team": p.get("latest_team") or "",
            "status": p.get("status") or "",
            "cols": [h for h, _f in cols],
            "reg": [pack(r) for r in rows if r["season_type"] == "REG"],
            "post": [pack(r) for r in rows if r["season_type"] == "POST"],
        }
        for k in ("college", "height", "weight", "birth_date", "draft_year", "draft_round",
                  "draft_pick", "draft_team", "jersey_number", "rookie_season"):  # fmt: skip
            if p.get(k) not in (None, ""):
                entry[k] = _round(p[k])
        if log_rows.get(pid):
            entry["log"] = [
                [w["week"], w.get("opponent") or "", w.get("season_type") or ""]
                + [_round(w.get(f, 0) or 0) for f in fields]
                for w in log_rows[pid]
            ]
        shards[shard_of(pid)][pid] = entry
        index_rows.append(
            [
                pid,
                entry["name"],
                entry["pos"],
                entry["team"],
                min(years),
                max(years),
                shard_of(pid),
            ]  # fmt: skip
        )
    index_rows.sort(key=lambda r: (-r[5], r[1], r[0]))

    leaders = []
    for label, field in LEADER_STATS:
        rows = [
            {**r, "href": _shard_href(r["player_id"])}
            for r in db.nfl_player_seasons.find(
                {"season": season, "season_type": "REG", field: {"$gt": 0}}
            )
            .sort([(field, -1), ("player_id", 1)])
            .limit(10)
        ]
        leaders.append({"label": label, "field": field, "rows": rows})
    write("players/index.html", "players.html", **common, index=index_rows, leaders=leaders)
    for i in range(SHARDS):
        write(f"players/s{i:02d}.html", "player_shard.html", **common, players=shards.get(i, {}),
              log_season=season)  # fmt: skip

    total = sum(p.stat().st_size for p in written)
    if log:
        log(f"site: {len(written)} pages, {total / 1e6:.1f} MB, season {season}", level="info")
    return {"files": len(written), "bytes": total, "season": season,
            "paths": sorted(os.path.relpath(p, out) for p in written)}  # fmt: skip
