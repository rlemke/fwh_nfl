"""Where the NFL domain keeps its downloads: the shared domain storage layer.

On the fleet this is the object store (``s3://…/cache/nfl``); on a laptop a
local directory. Raw nflverse files are kept as fetched so a reload never has
to go back to the network.
"""

from __future__ import annotations

from facetwork.domains.storage import domain_storage, join

S = domain_storage("nfl", layout="flat")


def raw_dir() -> str:
    return join(S.cache_root(), "raw")
