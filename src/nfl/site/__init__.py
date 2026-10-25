"""Static website rendering for the NFL domain."""

from .build import SHARDS, build_site, shard_of

__all__ = ["SHARDS", "build_site", "shard_of"]
