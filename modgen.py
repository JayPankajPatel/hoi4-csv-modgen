#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Generate HOI4 event script + localisation from CSV tables.

Reads  data/events.csv, data/options.csv, data/branches.csv, config.ini
Writes <out>/events/<mod>_events.txt
       <out>/localisation/english/<mod>_events_l_english.yml
       <out>/common/on_actions/<mod>_on_actions.txt   (only if fired_by uses an on_action)
where <out> is mod_path from config.ini, or output/<mod>/ if it is not set.
"""

import configparser
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"

EVENT_TYPES = {"country_event", "news_event", "state_event", "unit_leader_event"}
ID_RE = re.compile(r"^[A-Za-z0-9_]+\.\d+$")
# lowercase, not all digits (so a leftover option number is caught), not t/d (title/desc keys)
KEY_RE = re.compile(r"^(?!\d+$)[a-z0-9_]+$")
RESERVED_KEYS = {"t", "d"}
MOD_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")
ON_ACTION_RE = re.compile(r"^on_[a-z0-9_]+$")
# on_actions that run with no country in scope, so the event needs fire_scope to have a receiver
SCOPELESS_ON_ACTIONS = {"on_startup"}
# which kind of scope each event type runs in; firing across kinds needs a `scope`
SCOPE_CLASS = {
    "country_event": "country",
    "news_event": "country",
    "state_event": "state",
    "unit_leader_event": "unit leader",
}
SCOPE_HINT = {"country": "owner", "state": "capital_scope", "unit leader": "a unit leader scope"}
SCOPE_RE = re.compile(r"^[A-Za-z0-9_.:@]+$")
GENERATED = "# GENERATED FILE - do not edit. Change the CSVs and re-run the generator."


# ---------------------------------------------------------------- helpers
def read_csv(name, required):
    path = DATA / name
    if not path.exists():
        sys.exit(f"ERROR: missing file {path}")
    # utf-8-sig strips the BOM that Excel adds
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = [c for c in required if c not in (reader.fieldnames or [])]
        if missing:
            sys.exit(f"ERROR: {name} is missing column(s): {', '.join(missing)}")
        rows = []
        for i, row in enumerate(reader, start=2):  # row 1 is the header
            row = {k.strip(): (v or "").strip() for k, v in row.items() if k}
            if not any(row.values()):
                continue  # skip blank lines
            row["_line"] = i
            rows.append(row)
    return rows


TRUE_WORDS = {"yes", "y", "true", "1"}
FALSE_WORDS = {"no", "n", "false", "0"}


def parse_bool(value):
    """'' -> None, yes-ish -> True, no-ish -> False, anything else -> ValueError."""
    v = value.lower()
    if v == "":
        return None
    if v in TRUE_WORDS:
        return True
    if v in FALSE_WORDS:
        return False
    raise ValueError(value)


def brace_counts(line):
    """Count { and } outside quoted strings and # comments."""
    opens = closes = 0
    in_str = False
    for ch in line:
        if ch == '"':
            in_str = not in_str
        elif in_str:
            continue
        elif ch == "#":
            break
        elif ch == "{":
            opens += 1
        elif ch == "}":
            closes += 1
    return opens, closes


def brace_problem(text):
    """Return a description of unbalanced braces in a raw script cell, or None."""
    depth = 0
    for line in text.split("\n"):
        opens, closes = brace_counts(line)
        depth += opens - closes
        if depth < 0:
            return "has a } with no matching {"
    if depth > 0:
        return f"is missing {depth} closing }}"
    return None


def fmt_block(text, indent):
    """Re-indent a raw script snippet using brace depth."""
    out, depth = [], 0
    for line in text.replace("\r", "").split("\n"):
        line = line.strip()
        if not line:
            continue
        opens, closes = brace_counts(line)
        lead_close = len(line) - len(line.lstrip("}"))
        d = max(depth - lead_close, 0)
        out.append("\t" * (indent + d) + line)
        depth += opens - closes
    return out


def loc_escape(text):
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


# ---------------------------------------------------------------- load
def load():
    events = read_csv("events.csv", ["id", "title", "desc"])
    options = read_csv("options.csv", ["event_id", "key", "name"])
    branches = read_csv("branches.csv", ["from_event", "from_option", "to_event"])
    return events, options, branches


# ---------------------------------------------------------------- validate
def validate(events, options, branches):
    errors, warnings = [], []
    ev = {}
    bad_ids, bad_keys = set(), set()  # already reported; references to them are skipped quietly

    def check_script(loc, row, cols):
        for col in cols:
            problem = row.get(col) and brace_problem(row[col])
            if problem:
                errors.append(f"{loc}: {col} {problem}")

    for e in events:
        loc = f"events.csv line {e['_line']}"
        if not ID_RE.match(e["id"]):
            errors.append(f"{loc}: id '{e['id']}' must look like namespace.number (e.g. mymod.1)")
            bad_ids.add(e["id"])
            continue
        if e["id"] in ev:
            errors.append(f"{loc}: duplicate event id '{e['id']}' (first used on line {ev[e['id']]['_line']})")
            continue
        if not e["title"]:
            errors.append(f"{loc}: event '{e['id']}' has no title")
        if not e["desc"]:
            warnings.append(f"{loc}: event '{e['id']}' has no description")
        etype = e.get("type") or "country_event"
        if etype not in EVENT_TYPES:
            errors.append(f"{loc}: type '{etype}' must be one of {', '.join(sorted(EVENT_TYPES))}")
        e["_type"] = etype

        flags = {}
        for col in ("triggered_only", "fire_only_once", "hidden"):
            try:
                flags[col] = parse_bool(e.get(col, ""))
            except ValueError:
                errors.append(f"{loc}: {col} '{e[col]}' must be yes, no, or blank")
                flags[col] = None
        e["_fire_only_once"] = bool(flags["fire_only_once"])
        e["_hidden"] = bool(flags["hidden"])

        # fired_by decides how the event starts; triggered_only must agree with it
        triggered = flags["triggered_only"]
        fired_by = e.get("fired_by", "")
        mtth = e.get("mtth_days", "")
        if fired_by == "mtth":
            if triggered:
                errors.append(
                    f"{loc}: fired_by = mtth means the event fires on its own; "
                    f"set triggered_only to no or leave it blank"
                )
            triggered = False
            if not mtth:
                errors.append(f"{loc}: fired_by = mtth needs mtth_days")
        elif ON_ACTION_RE.match(fired_by):
            if triggered is False:
                errors.append(
                    f"{loc}: fired_by = {fired_by} fires the event directly; "
                    f"set triggered_only to yes or leave it blank"
                )
            triggered = True
            if fired_by in SCOPELESS_ON_ACTIONS and not e.get("fire_scope"):
                errors.append(
                    f"{loc}: {fired_by} has no country in scope; set fire_scope to who gets the event "
                    f"(e.g. GER, or every_country and limit it with trigger)"
                )
            elif etype not in ("country_event", "news_event") and not e.get("fire_scope"):
                warnings.append(
                    f"{loc}: {etype} '{e['id']}' is fired from {fired_by}; check that on_action runs in the right scope"
                )
        elif fired_by not in ("", "external"):
            errors.append(
                f"{loc}: fired_by '{fired_by}' must be blank, external, mtth, or an on_action such as on_startup"
            )
        if triggered is None:
            triggered = True
        if mtth:
            if not re.fullmatch(r"[1-9]\d*", mtth):
                errors.append(f"{loc}: mtth_days '{mtth}' must be a whole number of days")
            elif triggered:
                errors.append(f"{loc}: mtth_days does nothing on a triggered-only event; set fired_by = mtth")
        elif not triggered and fired_by != "mtth":
            warnings.append(f"{loc}: event '{e['id']}' is not triggered-only but has no mtth_days")
        e["_triggered_only"] = triggered
        e["_fired_by"] = fired_by
        fire_scope = e.get("fire_scope", "")
        if fire_scope and not SCOPE_RE.match(fire_scope):
            errors.append(f"{loc}: fire_scope '{fire_scope}' must be a single scope, e.g. GER or every_country")
        elif fire_scope and not ON_ACTION_RE.match(fired_by):
            errors.append(f"{loc}: fire_scope only applies when fired_by is an on_action")
        check_script(loc, e, ("trigger", "immediate", "extra"))
        ev[e["id"]] = e

    opts = defaultdict(list)
    for o in options:
        loc = f"options.csv line {o['_line']}"
        if o["event_id"] in bad_ids:
            continue
        if o["event_id"] not in ev:
            errors.append(f"{loc}: event_id '{o['event_id']}' does not exist in events.csv")
            continue
        key = o["key"]
        if not KEY_RE.match(key) or key in RESERVED_KEYS:
            errors.append(
                f"{loc}: key '{key}' must be lowercase letters, digits and _, not only digits, "
                f"and not 't' or 'd' (e.g. mobilize)"
            )
            bad_keys.add((o["event_id"], key))
            continue
        if any(other["key"] == key for other in opts[o["event_id"]]):
            errors.append(f"{loc}: event '{o['event_id']}' already has an option with key '{key}'")
            continue
        if not o["name"]:
            errors.append(f"{loc}: option '{key}' of '{o['event_id']}' has no name")
        if o.get("order") and not re.fullmatch(r"-?\d+", o["order"]):
            errors.append(f"{loc}: order '{o['order']}' must be a whole number")
            o["_bad_order"] = True  # already reported; keep the option so later checks don't cascade
        check_script(loc, o, ("effects", "trigger", "extra"))
        if o.get("ai_chance") and not re.fullmatch(r"-?\d+(\.\d+)?", o["ai_chance"]):
            check_script(loc, o, ("ai_chance",))
        opts[o["event_id"]].append(o)

    for eid, olist in opts.items():
        if any(o.get("_bad_order") for o in olist):
            continue
        with_order = [o for o in olist if o.get("order")]
        if with_order and len(with_order) != len(olist):
            errors.append(
                f"options.csv: event '{eid}' has an order on some options but not all; fill in every row or none"
            )
        elif with_order:
            olist.sort(key=lambda o: int(o["order"]))  # stable: ties keep row order

    for eid, e in ev.items():
        if not opts[eid] and not e["_hidden"]:
            errors.append(f"events.csv line {e['_line']}: event '{eid}' has no options in options.csv")
        if e["_hidden"] and len(opts[eid]) > 1:
            warnings.append(
                f"events.csv line {e['_line']}: hidden event '{eid}' has {len(opts[eid])} options; "
                f"nobody sees the choice"
            )

    incoming = set()
    for b in branches:
        loc = f"branches.csv line {b['_line']}"
        src, to = b["from_event"], b["to_event"]
        if src in bad_ids or to in bad_ids or (src, b["from_option"]) in bad_keys:
            continue
        if src not in ev:
            errors.append(f"{loc}: from_event '{src}' does not exist")
            continue
        if to not in ev:
            errors.append(f"{loc}: to_event '{to}' does not exist (fired from '{src}' option {b['from_option']})")
            continue
        keys = [o["key"] for o in opts[src]]
        if b["from_option"] not in keys:
            hint = " (from_option takes the option's key, not its number)" if b["from_option"].isdigit() else ""
            errors.append(
                f"{loc}: from_option '{b['from_option']}' is not an option key of '{src}'{hint}; "
                f"its keys are: {', '.join(keys) or 'none'}"
            )
            continue
        for col in ("days", "hours", "random_days"):
            if b.get(col) and not re.fullmatch(r"\d+", b[col]):
                errors.append(f"{loc}: {col} '{b[col]}' must be a whole number")
        scope = b.get("scope", "")
        src_class, to_class = SCOPE_CLASS.get(ev[src]["_type"]), SCOPE_CLASS.get(ev[to]["_type"])
        if scope and not SCOPE_RE.match(scope):
            errors.append(f"{loc}: scope '{scope}' must be a single scope, e.g. capital_scope, GER, 64")
        elif not scope and src_class and to_class and src_class != to_class:
            errors.append(
                f"{loc}: '{to}' is a {ev[to]['_type']} but '{src}' is a {ev[src]['_type']}; "
                f"set scope to say which {to_class} gets it (e.g. {SCOPE_HINT[to_class]})"
            )
        check_script(loc, b, ("condition",))
        incoming.add(to)

    for eid, e in ev.items():
        if e["_triggered_only"] and not e["_fired_by"] and eid not in incoming:
            warnings.append(
                f"event '{eid}' is triggered-only but nothing fires it; set fired_by "
                f"(e.g. on_startup, or external if a focus/decision fires it)"
            )
    return errors, warnings, ev, opts


# ---------------------------------------------------------------- generate
def gen_events(ev, opts, branches):
    out_by = defaultdict(lambda: defaultdict(list))  # event -> option key -> branches
    for b in branches:
        out_by[b["from_event"]][b["from_option"]].append(b)

    namespaces = []
    for eid in ev:
        ns = eid.split(".")[0]
        if ns not in namespaces:
            namespaces.append(ns)

    lines = [GENERATED, ""]
    lines += [f"add_namespace = {ns}" for ns in namespaces]
    lines.append("")

    for eid, e in ev.items():
        lines.append(f"{e['_type']} = {{")
        lines.append(f"\tid = {eid}")
        lines.append(f"\ttitle = {eid}.t")
        lines.append(f"\tdesc = {eid}.d")
        if e.get("picture"):
            lines.append(f"\tpicture = {e['picture']}")
        if e["_hidden"]:
            lines.append("\thidden = yes")
        if e["_triggered_only"]:
            lines.append("\tis_triggered_only = yes")
        if e["_fire_only_once"]:
            lines.append("\tfire_only_once = yes")
        if e.get("mtth_days"):
            lines.append(f"\tmean_time_to_happen = {{ days = {e['mtth_days']} }}")
        if e.get("trigger"):
            lines.append("\ttrigger = {")
            lines += fmt_block(e["trigger"], 2)
            lines.append("\t}")
        if e.get("immediate"):
            lines.append("\timmediate = {")
            lines += fmt_block(e["immediate"], 2)
            lines.append("\t}")
        if e.get("extra"):
            lines += fmt_block(e["extra"], 1)

        for o in opts[eid]:
            lines.append("\toption = {")
            lines.append(f"\t\tname = {eid}.{o['key']}")
            if o.get("trigger"):
                lines.append("\t\ttrigger = {")
                lines += fmt_block(o["trigger"], 3)
                lines.append("\t\t}")
            if o.get("ai_chance"):
                ai = o["ai_chance"]
                if re.fullmatch(r"-?\d+(\.\d+)?", ai):
                    lines.append(f"\t\tai_chance = {{ base = {ai} }}")
                else:
                    lines.append("\t\tai_chance = {")
                    lines += fmt_block(ai, 3)
                    lines.append("\t\t}")
            if o.get("effects"):
                lines += fmt_block(o["effects"], 2)
            for b in out_by[eid].get(o["key"], []):
                target_type = ev[b["to_event"]]["_type"]
                parts = [f"id = {b['to_event']}"]
                for col in ("days", "hours", "random_days"):
                    if b.get(col):
                        parts.append(f"{col} = {b[col]}")
                call = f"{target_type} = {{ {' '.join(parts)} }}"
                if b.get("scope"):
                    call = f"{b['scope']} = {{ {call} }}"
                if b.get("condition"):
                    lines.append("\t\tif = {")
                    lines.append("\t\t\tlimit = {")
                    lines += fmt_block(b["condition"], 4)
                    lines.append("\t\t\t}")
                    lines.append(f"\t\t\t{call}")
                    lines.append("\t\t}")
                else:
                    lines.append(f"\t\t{call}")
            if o.get("extra"):
                lines += fmt_block(o["extra"], 2)
            lines.append("\t}")
        lines.append("}")
        lines.append("")
    return "\n".join(lines)


def gen_on_actions(ev):
    by_action = defaultdict(list)
    for eid, e in ev.items():
        if ON_ACTION_RE.match(e["_fired_by"]):
            call = f"{e['_type']} = {{ id = {eid} }}"
            if e.get("fire_scope"):
                call = f"{e['fire_scope']} = {{ {call} }}"
            by_action[e["_fired_by"]].append(call)
    if not by_action:
        return None
    lines = [GENERATED, "", "on_actions = {"]
    for action, calls in by_action.items():
        lines += [f"\t{action} = {{", "\t\teffect = {"]
        lines += [f"\t\t\t{c}" for c in calls]
        lines += ["\t\t}", "\t}"]
    lines.append("}")
    return "\n".join(lines) + "\n"


def gen_loc(ev, opts):
    lines = ["l_english:"]
    for eid, e in ev.items():
        lines.append(f' {eid}.t:0 "{loc_escape(e["title"])}"')
        lines.append(f' {eid}.d:0 "{loc_escape(e["desc"])}"')
        for o in opts[eid]:
            lines.append(f' {eid}.{o["key"]}:0 "{loc_escape(o["name"])}"')
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- main
def write(path, text, bom=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8-sig" if bom else "utf-8")


def main():
    cfg = configparser.ConfigParser()
    cfg.read(ROOT / "config.ini", encoding="utf-8")
    mod_name = cfg.get("mod", "name", fallback="my_mod").strip()
    if not MOD_NAME_RE.match(mod_name):
        sys.exit(f"ERROR: config.ini name '{mod_name}' must be letters, digits and _ only (it is used in file names)")
    mod_path = cfg.get("mod", "mod_path", fallback="").strip()

    events, options, branches = load()
    errors, warnings, ev, opts = validate(events, options, branches)

    for w in warnings:
        print("WARNING:", w)
    if errors:
        print()
        for err in errors:
            print("ERROR:", err)
        sys.exit(f"\n{len(errors)} error(s) found. Nothing was generated. Fix the CSVs and run again.")

    if mod_path:
        out = Path(mod_path).expanduser()
        if not out.is_absolute():
            out = ROOT / out
        if not out.is_dir():
            sys.exit(f"ERROR: config.ini mod_path '{mod_path}' is not an existing folder")
    else:
        out = ROOT / "output" / mod_name

    # Only ever touch files named after this mod, so other files in the mod folder are safe.
    write(out / "events" / f"{mod_name}_events.txt", gen_events(ev, opts, branches))
    # HOI4 localisation must be UTF-8 WITH BOM, and the name must end in _l_english.yml
    write(out / "localisation" / "english" / f"{mod_name}_events_l_english.yml", gen_loc(ev, opts), bom=True)
    on_actions_file = out / "common" / "on_actions" / f"{mod_name}_on_actions.txt"
    on_actions = gen_on_actions(ev)
    if on_actions:
        write(on_actions_file, on_actions)
    elif on_actions_file.exists() and on_actions_file.read_text(encoding="utf-8").startswith(GENERATED):
        on_actions_file.unlink()  # stale from an earlier run; it would still fire old events
    print(f"\nOK: {len(ev)} events, {sum(len(v) for v in opts.values())} options written to {out}")


if __name__ == "__main__":
    main()
