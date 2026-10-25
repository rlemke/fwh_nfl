"""Score the model against what happened — and against the betting line.

Every number here is computed on games the model had NOT seen: a game's
prediction is made from the ratings before it, and the ratings only move once
its result is in. The comparison with the market is made on exactly the same
games (those with a closing line), so neither side gets an easier sample.

Metrics:

- **accuracy** — picked the winner; a tie counts half.
- **Brier score** — mean squared error of the win probability (lower is
  better; always saying 50% scores 0.25).
- **log loss** — punishes confident misses harder than Brier does.
- **spread MAE** — mean absolute error of the predicted margin, in points.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from .elo import EloParams, RatedGame, rate, spread_to_prob
from .games import Game

_EPS = 1e-9


@dataclass
class Score:
    n: int = 0
    correct: float = 0.0
    brier: float = 0.0
    log_loss: float = 0.0
    abs_err: float = 0.0

    def add(self, p_home: float, spread: float, margin: int) -> None:
        outcome = 1.0 if margin > 0 else 0.0 if margin < 0 else 0.5
        self.n += 1
        if outcome == 0.5:
            self.correct += 0.5
        elif (p_home > 0.5) == (outcome == 1.0):
            self.correct += 1.0
        elif p_home == 0.5:
            self.correct += 0.5
        self.brier += (p_home - outcome) ** 2
        p = min(max(p_home, _EPS), 1 - _EPS)
        self.log_loss -= outcome * math.log(p) + (1 - outcome) * math.log(1 - p)
        self.abs_err += abs(spread - margin)

    def summary(self) -> dict:
        if not self.n:
            return {"n": 0}
        return {
            "n": self.n,
            "accuracy": round(self.correct / self.n, 4),
            "brier": round(self.brier / self.n, 4),
            "log_loss": round(self.log_loss / self.n, 4),
            "spread_mae": round(self.abs_err / self.n, 2),
        }


def score_games(rated: Iterable[RatedGame], first_season: int, last_season: int) -> dict:
    """Model vs market, overall and per season, on the games both priced."""
    model, market = Score(), Score()
    by_season: dict[int, tuple[Score, Score]] = defaultdict(lambda: (Score(), Score()))
    buckets: dict[int, list[float]] = defaultdict(lambda: [0, 0.0, 0.0])
    model_all = Score()

    for rg in rated:
        g = rg.game
        if not g.played or not (first_season <= g.season <= last_season):
            continue
        model_all.add(rg.p_home, rg.spread, g.margin)
        if g.spread_line is None:
            continue
        m, k = by_season[g.season]
        for s in (model, m):
            s.add(rg.p_home, rg.spread, g.margin)
        p_mkt = spread_to_prob(g.spread_line)
        for s in (market, k):
            s.add(p_mkt, g.spread_line, g.margin)
        # Calibration: of the games the model called 60–70%, how many won?
        fav_p = max(rg.p_home, 1 - rg.p_home)
        fav_won = (g.margin > 0) if rg.p_home >= 0.5 else (g.margin < 0)
        b = min(int(fav_p * 10), 9)
        buckets[b][0] += 1
        buckets[b][1] += fav_p
        buckets[b][2] += 1.0 if fav_won else (0.5 if g.margin == 0 else 0.0)

    return {
        "first_season": first_season,
        "last_season": last_season,
        "model": model.summary(),
        "market": market.summary(),
        "model_all_games": model_all.summary(),
        "seasons": [
            {"season": s, "model": m.summary(), "market": k.summary()}
            for s, (m, k) in sorted(by_season.items())
        ],
        "calibration": [
            {
                "bucket": f"{b * 10}-{b * 10 + 10}%",
                "games": int(n),
                "predicted": round(sp / n, 3),
                "actual": round(won / n, 3),
            }
            for b, (n, sp, won) in sorted(buckets.items())
            if n
        ],
    }


def backtest(
    games: list[Game], first_season: int, last_season: int, params: EloParams | None = None
) -> dict:
    run = rate(games, params)
    out = score_games(run.games, first_season, last_season)
    out["params"] = run.params.to_dict()
    return out


def tune(games: list[Game], first_season: int = 2005, last_season: int = 2014) -> list[dict]:
    """Grid-search the constants on a TRAINING window; returns rows best first.

    Run once, pin the winner as the :class:`EloParams` defaults, and report the
    backtest on seasons after the window. Choosing constants on the same games
    you report would make the published accuracy a fitted number.
    """
    rows = []
    for k in (16.0, 20.0, 24.0):
        for hfa in (30.0, 40.0, 48.0, 55.0, 65.0):
            for regress in (0.25, 1.0 / 3.0, 0.4, 0.5):
                p = EloParams(k=k, hfa=hfa, regress=regress)
                s = score_games(rate(games, p).games, first_season, last_season)["model"]
                rows.append({"k": k, "hfa": hfa, "regress": round(regress, 3), **s})
    rows.sort(key=lambda r: r["brier"])
    return rows
