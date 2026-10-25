# FFL examples — NFL

Every block below compiles against this repo's FFL:

```bash
fw ffl compile --check --primary example.ffl \
  --library src/nfl/ffl/nfl.ffl --library <facetwork>/facetwork/ffl/http.ffl
```

Re-check them after changing a facet signature.

## 1. The minimal workflow — run the nightly update and report the favourite

```ffl
namespace my.nfl {
    use nfl.workflows

    workflow Nightly() => (favourite: String, chance: Double) andThen {
        run = nfl.workflows.Update()
        yield Nightly(favourite = run.favourite, chance = run.favourite_p)
    }
}
```

## 2. Re-rate with different constants, then simulate

`Rate`'s parameters change the model for every later step that reads the
ratings, so this is how to try a different home-field value.

```ffl
namespace my.nfl {
    use nfl.model
    use nfl.season

    workflow RateThenSimulate(sims: Int = 20000) => (key: String, favourite: String) andThen {
        rated = nfl.model.Rate(hfa = 40.0)
        plan = nfl.season.Plan(season = rated.season, sims = $.sims, chunk = 5000)
        sims = nfl.season.SimulateAll(key = plan.key, chunks = plan.chunks, width = 4)
        agg = nfl.season.Aggregate(key = plan.key, parts = sims.parts)
        yield RateThenSimulate(key = plan.key, favourite = agg.favourite)
    }
}
```

## 3. A wider fan-out with `foreach … limit` and `+=` aggregation

`SimulateChunk` returns counts that merge by addition, so the fan-out yields
with `+=`. With bare `=` only the last chunk would survive.

```ffl
namespace my.nfl {
    use nfl.season

    facet Spread(key: String, chunks: Json) => (parts: Json)
        andThen foreach c in $.chunks limit 8 {
            one = nfl.season.SimulateChunk(key = $.key, seed = $.c.seed, sims = $.c.sims)
            yield Spread(parts += one.counts)
        }

    workflow WideSimulation(season: Int = 0) => (favourite: String) andThen {
        plan = nfl.season.Plan(season = $.season, sims = 50000, chunk = 2500)
        spread = Spread(key = plan.key, chunks = plan.chunks)
        agg = nfl.season.Aggregate(key = plan.key, parts = spread.parts)
        yield WideSimulation(favourite = agg.favourite)
    }
}
```

## 4. `after` for a dependency the compiler cannot see

`Load` writes to MongoDB and `Rate` reads from it. No value flows between the
two steps, so `after` orders them.

```ffl
namespace my.nfl {
    use nfl.data
    use nfl.model

    workflow RecentHistoryOnly(first_season: Int = 2015) => (loaded: Int, through: String) andThen {
        src = nfl.data.Sources(first_season = $.first_season)
        fetched = nfl.data.FetchAll(sources = src.sources, width = 4)
        loaded = nfl.data.Load(paths = fetched.paths)
        rated = nfl.model.Rate() after loaded
        yield RecentHistoryOnly(loaded = loaded.loaded_count, through = rated.through_game)
    }
}
```

## 5. `when` on a step's result

```ffl
namespace my.nfl {
    use nfl.model

    workflow CheckModel(threshold: Double = 0.62) => (verdict: String) andThen {
        bt = nfl.model.Backtest(first_season = 2021, sims = 100) andThen when {
            case $.model_accuracy < $$.threshold => {
                yield CheckModel(verdict = "below threshold: trust the betting line")
            }
            case _ => {
                yield CheckModel(verdict = "within the expected range")
            }
        }
    }
}
```

## Cheat sheet

| Want | Write |
|---|---|
| a field of a step | `step.field` |
| a parameter of this block's container | `$.param` |
| one level further out | `$$.param` |
| aggregate across a `foreach` | `yield F(xs += item)` |
| order behind a step with no value flowing | `x = F() after other` |
| cap concurrency | `foreach c in $.chunks limit 8` |
