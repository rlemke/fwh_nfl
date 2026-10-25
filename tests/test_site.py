"""The static site: every page renders, every internal link resolves, and an
unchanged database renders byte-identical output (so a daily publish with no
new results commits nothing)."""

from __future__ import annotations

import re
from pathlib import Path

from nfl import db as dbmod
from nfl import service
from nfl.handlers.nfl_handlers import handle
from nfl.load import load
from nfl.site import SHARDS, build_site, shard_of
from nfl.teams import ALL_TEAMS, TEAM_CONFERENCE, TEAM_DIVISION

from .test_service import _paths, db  # noqa: F401  (fixture)

HREF = re.compile(r'href="([^"#:\x27+ ]+\.html)(#[^"]*)?"')  # literal hrefs, not JS-built ones


def _season_with_odds(d) -> None:
    load(d, _paths())
    service.rate_and_store(d)
    for t in ALL_TEAMS:
        d[dbmod.TEAMS].update_one(
            {"_id": t},
            {
                "$setOnInsert": {
                    "name": t,
                    "conference": TEAM_CONFERENCE[t],
                    "division": TEAM_DIVISION[t],
                    "color": "#333333",
                }
            },  # fmt: skip
            upsert=True,
        )
    plan = handle({"_facet_name": "nfl.season.Plan", "season": 2025, "sims": 200, "chunk": 100})
    parts = [
        handle({"_facet_name": "nfl.season.SimulateChunk", "key": plan["key"], **c})["counts"]
        for c in plan["chunks"]
    ]
    handle({"_facet_name": "nfl.season.Aggregate", "key": plan["key"], "parts": parts})


def test_site_renders_with_resolvable_links(db, tmp_path):  # noqa: F811
    _season_with_odds(db)
    out = build_site(db, str(tmp_path / "a"))
    root = tmp_path / "a"
    assert out["files"] == 3 + len(ALL_TEAMS) + SHARDS
    index = (root / "index.html").read_text()
    assert "playoff odds" in index and "view FFL" in index

    missing = []
    for page in root.rglob("*.html"):
        for href, _frag in HREF.findall(page.read_text()):
            if not (page.parent / href).resolve().exists():
                missing.append(f"{page.relative_to(root)} -> {href}")
    assert not missing, missing[:10]

    # A player is in the shard its links point at.
    shard = (root / "players" / f"s{shard_of('00-1'):02d}.html").read_text()
    assert '"00-1"' in shard and "Pat Q" in shard


def test_site_output_is_deterministic(db, tmp_path):  # noqa: F811
    _season_with_odds(db)
    build_site(db, str(tmp_path / "a"))
    build_site(db, str(tmp_path / "b"))
    a = {p.relative_to(tmp_path / "a"): p.read_bytes() for p in (tmp_path / "a").rglob("*.html")}
    b = {p.relative_to(tmp_path / "b"): p.read_bytes() for p in (tmp_path / "b").rglob("*.html")}
    assert a == b


def test_site_build_handler_writes_to_storage(db, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setenv("FW_DATA_ROOT", str(tmp_path / "data"))
    _season_with_odds(db)
    out = handle({"_facet_name": "nfl.site.Build"})
    assert out["files"] == 3 + len(ALL_TEAMS) + SHARDS
    assert Path(out["output_dir"], "index.html").exists()
    assert Path(out["output_dir"], "teams", "KC.html").exists()
