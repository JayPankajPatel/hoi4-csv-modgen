#!/usr/bin/env python3
"""Generate HOI4 event script + localisation from CSV tables.

Reads  data/events.csv, data/options.csv, data/branches.csv
Writes output/<mod_name>/events/generated_events.txt
       output/<mod_name>/localisation/english/generated_events_l_english.yml
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


def yes(value, default=False):
    if value == "":
        return default
    return value.lower() in ("yes", "y", "true", "1")


def fmt_block(text, indent):
    """Re-indent a raw script snippet using brace depth."""
    out, depth = [], 0
    for line in text.replace("\r", "").split("\n"):
        line = line.strip()
        if not line:
            continue
        opens = line.count("{")
        closes = line.count("}")
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
    options = read_csv("options.csv", ["event_id", "name"])
    branches = read_csv("branches.csv", ["from_event", "from_option", "to_event"])
    return events, options, branches


# ---------------------------------------------------------------- validate
def validate(events, options, branches):
    errors, warnings = [], []
    ev = {}
    for e in events:
        loc = f"events.csv line {e['_line']}"
        if not ID_RE.match(e["id"]):
            errors.append(f"{loc}: id '{e['id']}' must look like namespace.number (e.g. mymod.1)")
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
        ev[e["id"]] = e

    opts = defaultdict(list)
    for o in options:
        loc = f"options.csv line {o['_line']}"
        if o["event_id"] not in ev:
            errors.append(f"{loc}: event_id '{o['event_id']}' does not exist in events.csv")
            continue
        if not o["name"]:
            errors.append(f"{loc}: option for '{o['event_id']}' has no name")
        opts[o["event_id"]].append(o)

    for eid, e in ev.items():
        if not opts[eid]:
            errors.append(f"events.csv line {e['_line']}: event '{eid}' has no options in options.csv")

    incoming = set()
    for b in branches:
        loc = f"branches.csv line {b['_line']}"
        src, to = b["from_event"], b["to_event"]
        if src not in ev:
            errors.append(f"{loc}: from_event '{src}' does not exist")
            continue
        if to not in ev:
            errors.append(f"{loc}: to_event '{to}' does not exist (fired from '{src}' option {b['from_option']})")
            continue
        try:
            n = int(b["from_option"])
            if not 1 <= n <= len(opts[src]):
                raise ValueError
        except ValueError:
            errors.append(
                f"{loc}: from_option '{b['from_option']}' is not valid for '{src}' "
                f"(it has {len(opts[src])} option(s); use 1-{len(opts[src])})"
            )
            continue
        for col in ("days", "hours", "random_days"):
            if b.get(col) and not re.fullmatch(r"\d+", b[col]):
                errors.append(f"{loc}: {col} '{b[col]}' must be a whole number")
        incoming.add(to)

    for eid, e in ev.items():
        triggered_only = yes(e.get("triggered_only", ""), True)
        if triggered_only and eid not in incoming and not e.get("fired_by_note"):
            warnings.append(
                f"event '{eid}' is triggered-only but nothing in branches.csv fires it "
                f"(fine if a focus/decision fires it; otherwise it can never happen)"
            )
    return errors, warnings, ev, opts


# ---------------------------------------------------------------- generate
def gen_events(ev, opts, branches):
    out_by = defaultdict(lambda: defaultdict(list))  # event -> option idx -> branches
    for b in branches:
        out_by[b["from_event"]][int(b["from_option"])].append(b)

    namespaces = []
    for eid in ev:
        ns = eid.split(".")[0]
        if ns not in namespaces:
            namespaces.append(ns)

    lines = ["# GENERATED FILE - do not edit. Change the CSVs and re-run the generator.", ""]
    lines += [f"add_namespace = {ns}" for ns in namespaces]
    lines.append("")

    for eid, e in ev.items():
        etype = e.get("type") or "country_event"
        lines.append(f"{etype} = {{")
        lines.append(f"\tid = {eid}")
        lines.append(f"\ttitle = {eid}.t")
        lines.append(f"\tdesc = {eid}.d")
        if e.get("picture"):
            lines.append(f"\tpicture = {e['picture']}")
        if yes(e.get("hidden", "")):
            lines.append("\thidden = yes")
        if yes(e.get("triggered_only", ""), True):
            lines.append("\tis_triggered_only = yes")
        if yes(e.get("fire_only_once", "")):
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

        for idx, o in enumerate(opts[eid], start=1):
            lines.append("\toption = {")
            lines.append(f"\t\tname = {eid}.{option_letter(idx)}")
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
            for b in out_by[eid].get(idx, []):
                target_type = ev[b["to_event"]].get("type") or "country_event"
                parts = [f"id = {b['to_event']}"]
                for col in ("days", "hours", "random_days"):
                    if b.get(col):
                        parts.append(f"{col} = {b[col]}")
                if b.get("condition"):
                    lines.append("\t\tif = {")
                    lines.append("\t\t\tlimit = {")
                    lines += fmt_block(b["condition"], 4)
                    lines.append("\t\t\t}")
                    lines.append(f"\t\t\t{target_type} = {{ {' '.join(parts)} }}")
                    lines.append("\t\t}")
                else:
                    lines.append(f"\t\t{target_type} = {{ {' '.join(parts)} }}")
            if o.get("extra"):
                lines += fmt_block(o["extra"], 2)
            lines.append("\t}")
        lines.append("}")
        lines.append("")
    return "\n".join(lines)


def option_letter(idx):
    # 1->a, 2->b ... 26->z
    return chr(ord("a") + idx - 1)


def gen_loc(ev, opts):
    lines = ["l_english:"]
    for eid, e in ev.items():
        lines.append(f' {eid}.t:0 "{loc_escape(e["title"])}"')
        lines.append(f' {eid}.d:0 "{loc_escape(e["desc"])}"')
        for idx, o in enumerate(opts[eid], start=1):
            lines.append(f' {eid}.{option_letter(idx)}:0 "{loc_escape(o["name"])}"')
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- main
def main():
    cfg = configparser.ConfigParser()
    cfg.read(ROOT / "config.ini", encoding="utf-8")
    mod_name = cfg.get("mod", "name", fallback="my_mod")

    events, options, branches = load()
    errors, warnings, ev, opts = validate(events, options, branches)

    for w in warnings:
        print("WARNING:", w)
    if errors:
        print()
        for err in errors:
            print("ERROR:", err)
        sys.exit(f"\n{len(errors)} error(s) found. Nothing was generated. Fix the CSVs and run again.")

    out = ROOT / "output" / mod_name
    (out / "events").mkdir(parents=True, exist_ok=True)
    (out / "localisation" / "english").mkdir(parents=True, exist_ok=True)

    (out / "events" / "generated_events.txt").write_text(
        gen_events(ev, opts, branches), encoding="utf-8"
    )
    # HOI4 localisation must be UTF-8 WITH BOM
    (out / "localisation" / "english" / "generated_events_l_english.yml").write_text(
        gen_loc(ev, opts), encoding="utf-8-sig"
    )
    print(f"\nOK: {len(ev)} events, {sum(len(v) for v in opts.values())} options written to {out}")


if __name__ == "__main__":
    main()
