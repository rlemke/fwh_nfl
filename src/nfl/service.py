"""The NFL domain's operations, over MongoDB. Handlers are thin calls into here.

Every operation reads its inputs from the database and writes its outputs back,
so each workflow step is independently re-runnable: running ``rate`` twice
leaves the same documents as running it once.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime

import pymongo

from . import db as dbmod
from .backtest import score_games
from .elo import EloParams, rate, spread_to_prob
from .games import Game
from .simulate import SeasonState, merge, odds, projected_bracket, season_state, simulate
from .sources import default_season
from .teams import ALL_TEAMS, FIRST_ALIGNED_SEASON, conference_of

Log = Callable[..., None] | None


class NflError(ValueError):
    """A request the data cannot answer (unknown season, nothing loaded, ...)."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def games_from_db(db, through_season: int | None = None) -> list[Game]:
    q = {"season": {"$lte": through_season}} if through_season else {}
    out = []
    for d in db[dbmod.GAMES].find(q, {"_id": 1, **dict.fromkeys(_GAME_FIELDS, 1)}):
        out.append(
            Game(
                game_id=d["_id"],
                **{f: d.get(f) for f in _GAME_FIELDS},
            )
        )
    if not out:
        raise NflError("no games loaded — run nfl.workflows.Update first")
    out.sort(key=lambda g: g.sort_key)
    return out


_GAME_FIELDS = (
    "season", "game_type", "week", "gameday", "gametime", "home", "away", "home_score",
    "away_score", "neutral", "home_rest", "away_rest", "spread_line", "home_abbr",
    "away_abbr", "home_qb", "away_qb",
)  # fmt: skip


def current_params(db) -> EloParams:
    doc = db[dbmod.META].find_one({"_id": "ratings"})
    return EloParams(**doc["params"]) if doc and doc.get("params") else EloParams()


# --------------------------------------------------------------------------
# Ratings
# --------------------------------------------------------------------------


def rate_and_store(db, params: EloParams | None = None, log: Log = None) -> dict:
    """Rate every game since 1999 and store each game's pre-game view."""
    params = params or EloParams()
    games = games_from_db(db)
    run = rate(games, params)

    ops = []
    history: dict[str, list] = {t: [] for t in ALL_TEAMS}
    last_played: Game | None = None
    for rg in run.games:
        g = rg.game
        model = {
            "elo_home": round(rg.home_elo, 1),
            "elo_away": round(rg.away_elo, 1),
            "p_home": round(rg.p_home, 4),
            "spread_pred": round(rg.spread, 1),
            "p_home_market": (
                round(spread_to_prob(g.spread_line), 4) if g.spread_line is not None else None
            ),
        }
        ops.append(pymongo.UpdateOne({"_id": g.game_id}, {"$set": {"model": model}}))
        if g.played:
            last_played = g
            for team, post in ((g.home, rg.home_elo_post), (g.away, rg.away_elo_post)):
                if team in history:
                    history[team].append([g.season, g.week, g.game_type, round(post, 1)])
        if len(ops) >= 2000:
            db[dbmod.GAMES].bulk_write(ops, ordered=False)
            ops = []
    if ops:
        db[dbmod.GAMES].bulk_write(ops, ordered=False)

    ranked = sorted(ALL_TEAMS, key=lambda t: -run.ratings.get(t, params.mean))
    for i, t in enumerate(ranked):
        db[dbmod.RATINGS].replace_one(
            {"_id": t},
            {
                "_id": t,
                "elo": round(run.ratings.get(t, params.mean), 1),
                "rank": i + 1,
                "conference": conference_of(t),
                "history": history[t],
            },
            upsert=True,
        )
    meta = {
        "_id": "ratings",
        "params": params.to_dict(),
        "season": run.season,
        "through_game": last_played.game_id if last_played else None,
        "through_date": last_played.gameday if last_played else None,
        "games_rated": len(run.games),
        "computed_at": _now(),
    }
    db[dbmod.META].replace_one({"_id": "ratings"}, meta, upsert=True)
    if log:
        log(
            f"Rated {len(run.games):,} games through {meta['through_game']}; "
            f"top: {', '.join(ranked[:3])}",
            level="success",
        )
    return {**meta, "top": ranked[:5]}


# --------------------------------------------------------------------------
# Live predictions — the honest track record
# --------------------------------------------------------------------------


def record_predictions(db, season: int = 0, today: date | None = None, log: Log = None) -> dict:
    """Freeze the model's view of each upcoming game; settle the finished ones.

    A prediction is overwritten only while its game is still in the future, so
    once a game is played the stored prediction is the last one made BEFORE it.
    The live track record is computed from these, never recomputed from the
    ratings after the fact — a retrospective recomputation would quietly use
    information (later ratings, a corrected schedule) the model did not have.
    """
    season = season or default_season()
    today_s = (today or date.today()).isoformat()
    recorded = settled = 0
    for d in db[dbmod.GAMES].find({"season": season, "model": {"$exists": True}}):
        played = d.get("home_score") is not None and d.get("away_score") is not None
        if not played and (d.get("gameday") or "") >= today_s:
            m = d["model"]
            db[dbmod.PREDICTIONS].update_one(
                {"_id": d["_id"]},
                {
                    "$set": {
                        "season": season,
                        "week": d["week"],
                        "game_type": d["game_type"],
                        "gameday": d.get("gameday"),
                        "home": d["home"],
                        "away": d["away"],
                        "p_home": m["p_home"],
                        "spread_pred": m["spread_pred"],
                        "spread_line": d.get("spread_line"),
                        "p_home_market": m.get("p_home_market"),
                        "predicted_at": _now(),
                    }
                },
                upsert=True,
            )
            recorded += 1
        elif played:
            res = db[dbmod.PREDICTIONS].update_one(
                {"_id": d["_id"], "margin": {"$exists": False}},
                {"$set": {"margin": d["home_score"] - d["away_score"], "settled_at": _now()}},
            )
            settled += res.modified_count
    if log:
        log(f"{season}: {recorded} upcoming predictions recorded, {settled} settled", level="info")
    return {"season": season, "recorded": recorded, "settled": settled}


def track_record(db, season: int) -> dict:
    """Live accuracy this season, from the frozen pre-game predictions."""
    from .backtest import Score

    model, market = Score(), Score()
    for p in db[dbmod.PREDICTIONS].find({"season": season, "margin": {"$exists": True}}):
        model.add(p["p_home"], p["spread_pred"], p["margin"])
        if p.get("spread_line") is not None:
            market.add(p["p_home_market"], p["spread_line"], p["margin"])
    return {"season": season, "model": model.summary(), "market": market.summary()}


# --------------------------------------------------------------------------
# Backtest
# --------------------------------------------------------------------------


def _as_of(games: list[Game], season: int, after_week: int) -> list[Game]:
    """History as it stood after ``after_week`` of ``season``: later results erased."""
    out = []
    for g in games:
        if g.season > season:
            continue
        if g.season == season and (g.postseason or g.week > after_week):
            g = replace(g, home_score=None, away_score=None)
        out.append(g)
    return out


def run_backtest(
    db,
    first_season: int = 2015,
    last_season: int = 0,
    sims: int = 1000,
    after_week: int = 9,
    log: Log = None,
) -> dict:
    """Game-level accuracy vs the market, plus how well the playoff odds held up."""
    games = games_from_db(db)
    params = current_params(db)
    complete = {g.season for g in games if g.game_type == "SB" and g.played}
    last = last_season or max(complete)
    if first_season < FIRST_ALIGNED_SEASON:
        raise NflError(f"backtest from {FIRST_ALIGNED_SEASON} onwards")
    result = score_games(rate(games, params).games, first_season, last)

    # Playoff-odds calibration: simulate each season from mid-season and score
    # the playoff probabilities against who actually got in.
    brier, n, buckets = 0.0, 0, {}
    for season in range(first_season, last + 1):
        hist = _as_of(games, season, after_week)
        state = season_state(hist, rate(hist, params).ratings, season, params)
        rows = odds(merge([simulate(state, sims, seed=season)]))
        made = {t for g in games if g.season == season and g.postseason for t in (g.home, g.away)}
        for r in rows:
            y = 1.0 if r["team"] in made else 0.0
            brier += (r["playoffs"] - y) ** 2
            n += 1
            b = min(int(r["playoffs"] * 10), 9)
            k = buckets.setdefault(b, [0, 0.0, 0.0])
            k[0] += 1
            k[1] += r["playoffs"]
            k[2] += y
    result["playoff_odds"] = {
        "after_week": after_week,
        "sims_per_season": sims,
        "team_seasons": n,
        "brier": round(brier / n, 4) if n else None,
        "calibration": [
            {
                "bucket": f"{b * 10}-{b * 10 + 10}%",
                "teams": c,
                "predicted": round(sp / c, 3),
                "actual": round(y / c, 3),
            }
            for b, (c, sp, y) in sorted(buckets.items())
        ],
    }
    result["params"] = params.to_dict()
    result["computed_at"] = _now()
    db[dbmod.META].replace_one({"_id": "backtest"}, {"_id": "backtest", **result}, upsert=True)
    if log:
        log(
            f"Backtest {first_season}-{last}: model {result['model'].get('accuracy')} vs market "
            f"{result['market'].get('accuracy')} on {result['model'].get('n')} games",
            level="success",
        )
    return result


# --------------------------------------------------------------------------
# Season simulation
# --------------------------------------------------------------------------


def plan_simulation(db, season: int = 0, sims: int = 10000, chunk: int = 2500) -> dict:
    """Capture the state to simulate from and split the work into chunks.

    The state is stored under its own hash and every chunk names that hash, so
    all chunks simulate the SAME starting point even if a refresh lands while
    they run — and a retried chunk with the same seed repeats exactly.
    """
    if sims <= 0 or chunk <= 0:
        raise NflError("sims and chunk must be positive")
    season = season or default_season()
    games = games_from_db(db, through_season=season)
    params = current_params(db)
    state = season_state(games, rate(games, params).ratings, season, params)
    body = json.dumps(state.to_dict(), sort_keys=True)
    key = hashlib.sha256(body.encode()).hexdigest()[:16]
    last = max(
        (g for g in games if g.season == season and g.played),
        key=lambda g: g.sort_key,
        default=None,
    )
    db[dbmod.SIM_INPUTS].replace_one(
        {"_id": key},
        {
            "_id": key,
            "season": season,
            "state": json.loads(body),
            "through_game": last.game_id if last else None,
            "through_date": last.gameday if last else None,
            "created_at": _now(),
        },
        upsert=True,
    )
    base = int(key[:8], 16)
    chunks, left, i = [], sims, 0
    while left > 0:
        n = min(chunk, left)
        chunks.append({"seed": base + i, "sims": n})
        left -= n
        i += 1
    return {"key": key, "season": season, "chunks": chunks, "remaining_games": len(state.remaining)}


def simulate_chunk(db, key: str, seed: int, sims: int) -> dict:
    doc = db[dbmod.SIM_INPUTS].find_one({"_id": key})
    if not doc:
        raise NflError(f"no simulation input {key!r}")
    return simulate(SeasonState.from_dict(doc["state"]), sims, seed)


def aggregate(db, key: str, parts: list[dict], log: Log = None) -> dict:
    doc = db[dbmod.SIM_INPUTS].find_one({"_id": key})
    if not doc:
        raise NflError(f"no simulation input {key!r}")
    merged = merge(parts)
    rows = odds(merged)
    top = sorted(merged["matchups"].items(), key=lambda kv: -kv[1])[:12]
    out = {
        "_id": key,
        "season": merged["season"],
        "sims": merged["sims"],
        "through_game": doc.get("through_game"),
        "through_date": doc.get("through_date"),
        "odds": rows,
        "bracket": projected_bracket(merged, merged["season"]),
        "matchups": [
            {"afc": m.split("-")[0], "nfc": m.split("-")[1], "p": round(c / merged["sims"], 4)}
            for m, c in top
        ],
        "created_at": _now(),
    }
    db[dbmod.ODDS].replace_one({"_id": key}, out, upsert=True)
    fav = rows[0]
    if log:
        log(
            f"{merged['sims']:,} simulations of {merged['season']}: favourite "
            f"{fav['team']} {fav['champion']:.1%}",
            level="success",
        )
    return {
        "season": merged["season"],
        "sims": merged["sims"],
        "favourite": fav["team"],
        "favourite_p": fav["champion"],
    }
