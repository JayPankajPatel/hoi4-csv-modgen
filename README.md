# HOI4 CSV Mod Generator

Write your events in spreadsheets, run one command, get Paradox script and localisation.

## Workflow

1. Edit the CSVs in `data/` (Excel / Google Sheets are fine; save as CSV).
2. Double-click `run.bat` (or run `python modgen.py`).
3. Copy the contents of `output/<mod_name>/` into your mod folder.
4. Launch HOI4 and check `Documents/Paradox Interactive/Hearts of Iron IV/logs/error.log` for script errors.

Never hand-edit the generated files. They are overwritten on every run.

Requires Python 3.8+. No extra packages.

Set the output folder name in `config.ini`.

## Tables

### `data/events.csv` (one row per event)

| Column | Required | Notes |
|---|---|---|
| `id` | yes | `namespace.number`, e.g. `mymod.1`. Must be unique. |
| `type` | no | `country_event` (default), `news_event`, `state_event`, `unit_leader_event` |
| `title` | yes | Event title text |
| `desc` | yes | Event description text |
| `picture` | no | GFX name, e.g. `GFX_report_event_generic_read_write` |
| `triggered_only` | no | `yes` (default) / `no` |
| `fire_only_once` | no | `yes` / `no` |
| `hidden` | no | `yes` / `no` |
| `mtth_days` | no | Mean time to happen in days (for non-triggered-only events) |
| `trigger` | no | Raw script: conditions, no outer `{ }` |
| `immediate` | no | Raw script: effects that run when the event fires |
| `extra` | no | Raw script added to the event body (escape hatch) |

### `data/options.csv` (one row per option; order = option order)

| Column | Required | Notes |
|---|---|---|
| `event_id` | yes | Must exist in `events.csv` |
| `name` | yes | Button text |
| `effects` | no | Raw script effects |
| `ai_chance` | no | A number (becomes `base = N`) or raw script |
| `trigger` | no | Raw script, makes the option conditional |
| `extra` | no | Raw script added to the option (escape hatch) |

Option 1 is the first row for that event, option 2 the second, and so on.

### `data/branches.csv` (what an option fires next)

| Column | Required | Notes |
|---|---|---|
| `from_event` | yes | Source event id |
| `from_option` | yes | Option number (1, 2, 3...) |
| `to_event` | yes | Event to fire |
| `days` / `hours` / `random_days` | no | Delay, whole numbers |
| `condition` | no | Raw script trigger; branch only fires if true |

An option can have several branches (several rows).

## Writing raw script cells

Effects and triggers are plain Paradox script. Put one statement per line inside the cell
(Alt+Enter in Excel, Ctrl+Enter in Google Sheets), without wrapping braces:

```
add_political_power = -25
if = {
    limit = { has_war = yes }
    add_stability = -0.05
}
```

The generator handles indentation.

## Localisation keys

Generated automatically:

- `mymod.1.t` title, `mymod.1.d` description
- `mymod.1.a`, `.b`, `.c`... option names, in row order

## Checks

Before writing anything, the generator reports plain-English errors (duplicate ids,
unknown event ids, bad option numbers, events with no options) and stops.
Warnings (such as an event nothing fires) don't stop the run.

It can't check game logic. Typos in effect names show up in HOI4's `error.log`.

## Encoding

CSV: UTF-8 (Excel's "CSV UTF-8" is fine). Output localisation is written as UTF-8 with BOM,
which HOI4 requires.
