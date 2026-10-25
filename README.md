# fwh_nfl — NFL players, teams, predictions and the path to the Super Bowl

A [Facetwork](https://github.com/rlemke/facetwork) domain. It loads every NFL
game since 1999 and every player's weekly stats from
[nflverse](https://github.com/nflverse). It rates the teams, predicts each
game, and simulates the rest of the season with the league's real
tiebreakers. The results show in the Facetwork dashboard at `/nfl`.

## What it measures — and how well

The model is an Elo team rating: FiveThirtyEight's published NFL method,
without its quarterback adjustment. It is scored on games it had not seen,
against the betting market's closing line on the same games.

| 2015–2025, 3,028 games | Model | Betting line |
|---|---|---|
| Picked the winner | 64.2% | 66.1% |
| Brier score (lower is better) | 0.221 | 0.212 |
| Mean miss on the final margin | 10.2 pts | 9.8 pts |

It is well calibrated: when it says 70–80%, the favourite wins about 72% of
the time. Playoff odds computed after week 9 have a Brier score of 0.129
over 352 team-seasons; teams it gave 90%+ made the playoffs 96% of the time.

**It does not beat the market, and it is not meant for betting.** It cannot
see quarterback changes, injuries or weather. When it and the line disagree
by more than a few points, the line usually knows something it does not.

There are two kinds of record:

- **Backtest.** Re-scores history game by game, using only results from
  before each game.
- **Live.** Uses predictions stored before kickoff and never rewritten
  afterwards, so the live record cannot benefit from hindsight.

## The season simulator

Each simulation plays the remaining schedule from the current ratings, which
move as simulated results come in. It then seeds both conferences with the
NFL's tiebreaking procedures and plays the bracket. The seeding code
reproduces **every real postseason since 2002**: who got in, the byes, every
wild-card pairing and host, and the host of every later game. That's 48 of
48 conference seedings, and a test checks it against live data.

Not implemented: the two "combined ranking of points" steps. When one of
them would have decided a tie, the code uses net points instead.

## FFL at a glance

```ffl
namespace my.nfl {
    use nfl.workflows

    workflow Nightly() => (favourite: String, chance: Double) andThen {
        run = nfl.workflows.Update()
        yield Nightly(favourite = run.favourite, chance = run.favourite_p)
    }
}
```

More in [docs/ffl-examples.md](docs/ffl-examples.md).

| Workflow | What it does |
|---|---|
| `nfl.workflows.Update` | fetch what changed → load → re-rate → freeze predictions → simulate the season |
| `nfl.workflows.Simulate` | re-simulate from what is loaded |
| `nfl.workflows.ScoreModel` | backtest vs the betting line, plus playoff-odds calibration |

The fetch step uses the built-in `fw.http.Fetch`, which revalidates with
conditional GETs. Loads skip any file whose checksum has not changed, so a
nightly run reloads the current season and skips the other 27.

## Running it

```bash
fw install domain nfl
fw ffl seed --include '^nfl$'
fw runner start --domain nfl -- --topics 'nfl.*'   # or the fleet's generalist runner
fw svc nfl-update                                  # one run now
fw svc nfl-update --install                        # daily at 06:30 local
```

Data lives in `FW_EXAMPLES_DATABASE` (default `facetwork_examples`), in the
collections `nfl_*`. Raw downloads go to the domain cache: the object store
on the fleet, a local directory elsewhere.

## Data

[nflverse](https://github.com/nflverse): `nfldata/games.csv` (schedules,
results, closing lines, starting quarterbacks), `players.csv`, and
`stats_player_week_<season>.csv`. Check nflverse's terms before
republishing derived data.
