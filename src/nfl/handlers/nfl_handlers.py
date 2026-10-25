"""Thin dispatchers for the NFL facets.

Each handler coerces the FFL payload, calls :mod:`nfl.service` (or the loader),
and shapes the return. All logic lives in those modules so the terminal, the
tests and the runtime exercise one code path.

One ``handle(payload)`` entry point with a ``_DISPATCH`` table, because the
runner's handler cache is keyed on (module, entrypoint): several facets sharing
a module must share one entrypoint.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .. import db as dbmod
from .. import service, sources
from ..elo import EloParams
from ..load import load


def _db():
    db = dbmod.get_db()
    dbmod.ensure_indexes(db)
    return db


def _json(value: Any, default: Any) -> Any:
    """FFL may hand structured values over as JSON text."""
    if value is None or value == "":
        return default
    if isinstance(value, str):
        return json.loads(value)
    return value


def _log(params: dict) -> Callable[..., None] | None:
    return params.get("_step_log")


def handle_sources(params: dict[str, Any]) -> dict[str, Any]:
    plan = sources.plan(int(params.get("first_season") or 0), int(params.get("last_season") or 0))
    log = _log(params)
    if log:
        log(f"{len(plan)} nflverse files to check", level="info")
    season = int(params.get("last_season") or 0) or sources.default_season()
    return {"sources": plan, "count": len(plan), "season": season}


def handle_load(params: dict[str, Any]) -> dict[str, Any]:
    paths = [p for p in _json(params.get("paths"), []) if p]
    out = load(_db(), paths, force=bool(params.get("force")), log=_log(params))
    return {
        "loaded": out["loaded"],
        "loaded_count": len(out["loaded"]),
        "skipped_count": len(out["skipped"]),
    }


def handle_rate(params: dict[str, Any]) -> dict[str, Any]:
    p = EloParams(
        k=float(params.get("k") or EloParams.k),
        hfa=float(params.get("hfa") if params.get("hfa") is not None else EloParams.hfa),
        regress=float(
            params.get("regress") if params.get("regress") is not None else EloParams.regress
        ),
    )
    out = service.rate_and_store(_db(), p, log=_log(params))
    return {
        "season": out["season"],
        "through_game": out["through_game"] or "",
        "games_rated": out["games_rated"],
        "top": out["top"],
    }


def handle_record_predictions(params: dict[str, Any]) -> dict[str, Any]:
    out = service.record_predictions(_db(), int(params.get("season") or 0), log=_log(params))
    return out


def handle_backtest(params: dict[str, Any]) -> dict[str, Any]:
    out = service.run_backtest(
        _db(),
        first_season=int(params.get("first_season") or 2015),
        last_season=int(params.get("last_season") or 0),
        sims=int(params.get("sims") or 1000),
        after_week=int(params.get("after_week") or 9),
        log=_log(params),
    )
    m, k = out["model"], out["market"]
    return {
        "games": m.get("n", 0),
        "model_accuracy": m.get("accuracy", 0.0),
        "market_accuracy": k.get("accuracy", 0.0),
        "model_brier": m.get("brier", 0.0),
        "market_brier": k.get("brier", 0.0),
        "playoff_brier": out["playoff_odds"]["brier"] or 0.0,
    }


def handle_plan(params: dict[str, Any]) -> dict[str, Any]:
    out = service.plan_simulation(
        _db(),
        season=int(params.get("season") or 0),
        sims=int(params.get("sims") or 10000),
        chunk=int(params.get("chunk") or 2500),
    )
    log = _log(params)
    if log:
        log(
            f"{out['season']}: {out['remaining_games']} games left; "
            f"{len(out['chunks'])} chunks from state {out['key']}",
            level="info",
        )
    return out


def handle_simulate_chunk(params: dict[str, Any]) -> dict[str, Any]:
    counts = service.simulate_chunk(_db(), params["key"], int(params["seed"]), int(params["sims"]))
    return {"counts": counts}


def handle_site_build(params: dict[str, Any]) -> dict[str, Any]:
    """Render the static site locally, then copy it to the domain's maps store.

    Staged locally because an object store has no partial writes; the copy is
    file by file, so a crash halfway leaves a mix of old and new pages that the
    next run overwrites (every page is regenerated every time).
    """
    from facetwork.domains.storage import is_remote, join

    from ..site import build_site
    from ..storage import S

    dest = join(S.maps_root(), "site")
    with tempfile.TemporaryDirectory(prefix="nfl_site_") as tmp:
        out = build_site(_db(), tmp, log=_log(params))
        for rel in out["paths"]:
            target = join(dest, rel)
            if not is_remote(target):
                os.makedirs(os.path.dirname(target), exist_ok=True)
            S.write_bytes(target, Path(tmp, rel).read_bytes())
    return {
        "output_dir": dest,
        "files": out["files"],
        "bytes": out["bytes"],
        "season": out["season"],
    }


def handle_aggregate(params: dict[str, Any]) -> dict[str, Any]:
    parts = _json(params.get("parts"), [])
    return service.aggregate(_db(), params["key"], parts, log=_log(params))


_DISPATCH: dict[str, Callable[[dict], dict]] = {
    "nfl.data.Sources": handle_sources,
    "nfl.data.Load": handle_load,
    "nfl.model.Rate": handle_rate,
    "nfl.model.RecordPredictions": handle_record_predictions,
    "nfl.model.Backtest": handle_backtest,
    "nfl.season.Plan": handle_plan,
    "nfl.season.SimulateChunk": handle_simulate_chunk,
    "nfl.season.Aggregate": handle_aggregate,
    "nfl.site.Build": handle_site_build,
}


def handle(payload: dict) -> dict:
    facet = payload["_facet_name"]
    handler = _DISPATCH.get(facet)
    if handler is None:
        raise ValueError(f"Unknown facet: {facet}")
    return handler(payload)


def register_handlers(runner) -> None:
    """RegistryRunner path — one module_uri/entrypoint pair per facet."""
    for facet_name in _DISPATCH:
        runner.register_handler(
            facet_name=facet_name,
            module_uri=f"file://{os.path.abspath(__file__)}",
            entrypoint="handle",
        )


def register_poller(poller) -> None:
    """Legacy AgentPoller path."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)
