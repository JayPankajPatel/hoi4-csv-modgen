# HOI4 CSV Mod Generator

Write your events in spreadsheets, run one command, get Paradox script and localisation.

## Workflow

1. Edit the CSVs in `data/` (Excel / Google Sheets are fine; save as CSV).
2. Double-click `run.bat` (or run `uv run modgen.py`).
3. If `mod_path` is set in `config.ini`, the files are already in your mod. Otherwise copy the
   contents of `output/<mod_name>/` into your mod folder.
4. Launch HOI4 and check `Documents/Paradox Interactive/Hearts of Iron IV/logs/error.log` for script errors.

Never hand-edit the generated files. They are overwritten on every run.

## Setup

Install [uv](https://docs.astral.sh/uv/) once. It downloads a suitable Python by itself, so you
don't need to install Python separately.

- Windows (PowerShell): `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
- macOS / Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`

Then run `uv run modgen.py` (or double-click `run.bat`). The Python version and dependencies
are declared at the top of `modgen.py`.

## `config.ini`

```ini
[mod]
name = my_mod
mod_path = C:/Users/you/Documents/Paradox Interactive/Hearts of Iron IV/mod/my_mod
```

- `name` (letters, digits, `_`) prefixes every generated file, so two mods built with this tool
  never overwrite each other's files.
- `mod_path` is optional. When set, files are written straight into that existing folder.
  Only these files are ever written there:
  - `events/<name>_events.txt`
  - `localisation/english/<name>_events_l_english.yml`
  - `common/on_actions/<name>_on_actions.txt` (only if an event uses an on_action in `fired_by`)

  Nothing else in the folder is touched.

## Tables

### `data/events.csv` (one row per event)

| Column | Required | Notes |
|---|---|---|
| `id` | yes | `namespace.number`, e.g. `mymod.1`. Must be unique. |
| `type` | no | `country_event` (default), `news_event`, `state_event`, `unit_leader_event` |
| `title` | yes | Event title text |
| `desc` | yes | Event description text |
| `picture` | no | GFX name, e.g. `GFX_report_event_generic_read_write` |
| `fired_by` | no | How the event starts; see below. |
| `triggered_only` | no | `yes` (default) / `no`. Usually leave blank and let `fired_by` decide. |
| `fire_only_once` | no | `yes` / `no` |
| `hidden` | no | `yes` / `no` |
| `mtth_days` | no | Mean time to happen in days. Requires `fired_by = mtth`. |
| `trigger` | no | Raw script: conditions, no outer `{ }` |
| `immediate` | no | Raw script: effects that run when the event fires |
| `extra` | no | Raw script added to the event body (escape hatch) |

#### `fired_by`: how a chain starts

Events reached from another event's option (via `branches.csv`) need nothing here. The
first event of a chain does:

| Value | Meaning |
|---|---|
| `on_startup` (or another `on_...` name) | Fired from that on_action. The generator writes the on_actions file for you. |
| `external` | Fired by something you write yourself (a focus, decision, another mod file). |
| `mtth` | Fires on its own at random; needs `mtth_days`. |
| blank | Only reached through `branches.csv`. |

A triggered-only event with a blank `fired_by` that nothing branches to gets a warning,
because it can never happen. on_actions like `on_startup` run for every country, so use the
event's `trigger` to limit who gets it (e.g. `tag = GER`).

Yes/no columns accept `yes`/`no` (also `y`/`n`, `true`/`false`, `1`/`0`). Anything else is
an error.

### `data/options.csv` (one row per option)

| Column | Required | Notes |
|---|---|---|
| `event_id` | yes | Must exist in `events.csv` |
| `key` | yes | Short id for the option, unique within its event, e.g. `mobilize`. Lowercase letters, digits, `_`. Not `t` or `d`. |
| `order` | no | Button order (whole number). Fill it for every option of an event or for none. |
| `name` | yes | Button text |
| `effects` | no | Raw script effects |
| `ai_chance` | no | A number (becomes `base = N`) or raw script |
| `trigger` | no | Raw script, makes the option conditional |
| `extra` | no | Raw script added to the option (escape hatch) |

Without `order`, buttons appear in row order. Options are identified by `key`, never by
position, so sorting the sheet can change button order but never which branch or text
belongs to which option.

### `data/branches.csv` (what an option fires next)

| Column | Required | Notes |
|---|---|---|
| `from_event` | yes | Source event id |
| `from_option` | yes | The option's `key`, e.g. `mobilize` |
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
- `mymod.1.<key>` option names, e.g. `mymod.1.mobilize`

## Checks

Before writing anything, the generator reports plain-English errors (duplicate ids,
unknown event ids, unknown option keys, events with no options, `fired_by` that contradicts
`triggered_only`) and stops.
Warnings (such as an event nothing fires) don't stop the run.

It can't check game logic. Typos in effect names show up in HOI4's `error.log`.

## Encoding

CSV: UTF-8 (Excel's "CSV UTF-8" is fine). Output localisation is written as UTF-8 with BOM,
which HOI4 requires.
