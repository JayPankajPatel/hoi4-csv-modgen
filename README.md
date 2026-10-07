# HOI4 CSV Mod Generator

[![Nightly](https://github.com/JayPankajPatel/hoi4-csv-modgen/actions/workflows/nightly.yml/badge.svg)](https://github.com/JayPankajPatel/hoi4-csv-modgen/actions/workflows/nightly.yml)

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

Then run `uv run modgen.py` (or double-click `run.bat`). To keep several mods apart, give each
its own folder with a `config.ini` and `data/`, and run `uv run modgen.py path/to/folder`. The Python version and dependencies
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
  - `gfx/event_pictures/<name>_<slug>.png` / `.dds` and `interface/<name>_event_pictures.gfx`
    (only if a `picture` is an image path)

  Nothing else in the folder is touched.

## Tables

### `data/events.csv` (one row per event)

| Column | Required | Notes |
|---|---|---|
| `id` | yes | `namespace.number`, e.g. `mymod.1`. Must be unique. |
| `type` | no | `country_event` (default), `news_event`, `state_event`, `unit_leader_event` |
| `title` | yes | Event title text |
| `desc` | yes | Event description text |
| `picture` | no | A sprite name (`GFX_report_event_generic_read_write`) or a path to your own `.png`/`.dds`, e.g. `pictures/crisis.png`. See [Custom pictures](#custom-pictures). |
| `fired_by` | no | How the event starts; see below. |
| `fire_scope` | no | Who receives the event when `fired_by` is an on_action, e.g. `GER` or `every_country`. Required for `on_startup`. |
| `triggered_only` | no | `yes` (default) / `no`. Usually leave blank and let `fired_by` decide. |
| `fire_only_once` | no | `yes` / `no` |
| `hidden` | no | `yes` / `no`. Hidden events need no title, description or option names, and may have no options (just `immediate`). |
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
because it can never happen.

`on_startup` runs once with no country in scope, so it needs `fire_scope`: a tag such as `GER`
sends the event to that country, while `every_country` sends it to all of them, and the
event's `trigger` decides who actually gets it. The sample generates:

```
on_startup = { effect = { GER = { country_event = { id = mymod.1 } } } }
```

Other on_actions have their own scopes; check the
[wiki's On actions page](https://hoi4.paradoxwikis.com/On_actions) before using one.

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
| `hidden` | no | `yes` wraps the call in `hidden_effect`, so the option's tooltip doesn't reveal the follow-up event. |
| `scope` | no | Who receives the event, e.g. `capital_scope`, `GER`, a state id. Required when the types differ (country/news event → state event or unit leader event). |

An option can have several branches (several rows).

With `scope`, the call is wrapped: `capital_scope = { state_event = { id = mymod.5 } }`.

## Custom pictures

Put the image in the project folder and write its path in `picture`, e.g. `pictures/crisis.png`
(`\` works too). A value is read as a path when it contains `/` or `\` or ends in an image
extension; anything else is a sprite name, as before.

The generator copies the image to `gfx/event_pictures/<name>_<slug>.png`, registers it in
`interface/<name>_event_pictures.gfx`, and uses the sprite `GFX_<name>_<slug>`. The slug comes
from the path: `pictures/War Room.png` → `GFX_my_mod_pictures_war_room`. Several events can
share one image.

- `.png` and `.dds` are copied unchanged. HOI4 reads PNG directly, so there's no conversion.
- Usual sizes: 210×176 for country events, 397×153 for news events. Other sizes give a warning.
- When a picture is no longer used, its copy is deleted. Only files listed in the generated
  `.gfx` and named `<name>_...` inside `gfx/event_pictures/` are ever removed.

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

The generator handles indentation and reports cells with unbalanced braces. Braces inside
`# comments` and `"quoted strings"` are ignored.

## Localisation keys

Generated automatically:

- `mymod.1.t` title, `mymod.1.d` description
- `mymod.1.<key>` option names, e.g. `mymod.1.mobilize`

## Checks

Before writing anything, the generator reports plain-English errors (duplicate ids,
unknown event ids, unknown option keys, events with no options, `fired_by` that contradicts
`triggered_only`, missing or unsupported picture files) and stops.
Warnings (such as an event nothing fires) don't stop the run.

It can't check game logic. Typos in effect names show up in HOI4's `error.log`.

## Encoding

CSV: UTF-8 (Excel's "CSV UTF-8" is fine). Output localisation is written as UTF-8 with BOM,
which HOI4 requires.

## Development

Developer tools (ruff, ty, pre-commit) are declared in `pyproject.toml` and pinned in `uv.lock`.
Users running the generator don't need them; `uv run modgen.py` uses only the script header.

```
uv sync                       # install dev tools into .venv
uv run pre-commit install     # run checks on every commit
uv run pre-commit run --all-files
```

The hooks run `ruff check --fix`, `ruff format` and `ty check`.

## Example

[`examples/kaiser_redux/`](examples/kaiser_redux/) rebuilds 18 Kaiserredux events from CSV and checks the
output against the originals.
