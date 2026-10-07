# Example: Kaiserredux events rebuilt from CSV

Eight event files from [Kaiserredux](https://github.com/JoeBidenWhatAreYouHiding/kx)
(commit `30dffc2`), written as CSVs and regenerated. The output matches the originals: all
18 events and 50 localisation keys, checked by `verify.py`.

Event script and text are from Kaiserredux and belong to the Kaiserredux and KR4 teams.
They're included only to show and test the generator.

## Run it

From the repository root:

```
uv run modgen.py examples/kaiser_redux
uv run examples/kaiser_redux/verify.py
```

`verify.py` downloads the original files at the pinned commit and compares them by meaning.
Comments, whitespace and field order inside an event or option are ignored, but option order
and the order of effects are not. Expected output:

```
script: 18 events compared, 0 difference(s)
localisation: 50 keys compared, 0 difference(s), BOM ok
```

## What's covered

| Original file | Events | What it exercises |
|---|---|---|
| `Kachin.txt` | 2 | Plain option effects, `immediate` |
| `city_capture_small.txt` | 1 | Option `trigger`, picture, multi-paragraph text with quotes |
| `Sierra_Leone.txt` | 1 | Event `trigger`, not triggered-only, `ai_chance` blocks, nested effects |
| `Ghana.txt` | 2 | A branch with a delay, wrapped in `hidden_effect` (`hidden = yes` in branches.csv) |
| `Flavor News.txt` | 5 | `news_event`, `major = yes` via `extra` |
| `Transylvania.txt` | 2 | `fired_by = mtth`, a branch sent to another country (`scope = ROM`) |
| `Kurdistan.txt` | 2 | Calls to events in other files (raw effects), escaped quotes in script strings |
| `Unit_Leaders.txt` | 3 | `unit_leader_event`, a hidden event with no text and a nameless option |

## Things worth knowing

- **Option keys are `a`, `b`, `c`** to match Kaiserredux's loc keys (`kachin.1.a`). Any key works.
- **Events in other files can't be branch targets.** `branches.csv` only knows events in this
  CSV set, so Kurdistan's `worldnews.427` call and Unit_Leaders' random-leader picker are written
  as raw effects. Those target events get `fired_by = external`.
- **Kaiserredux has no localisation for `flavornews.3`–`5`**, so in the game those events show raw
  keys. The generator refuses to build an event without a title, so the example uses `TODO`
  placeholders, and `verify.py` skips those keys.
- **`\"` vs `"` inside text:** the generator writes `\"`. Kaiserredux uses both forms, so both
  work in game.
