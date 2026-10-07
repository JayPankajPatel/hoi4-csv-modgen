#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Generate HOI4 event script + localisation from CSV tables.

Reads  data/events.csv, data/options.csv, data/branches.csv, config.ini
Writes <out>/events/<mod>_events.txt
       <out>/localisation/english/<mod>_events_l_english.yml
       <out>/common/on_actions/<mod>_on_actions.txt
           (only if fired_by uses an on_action)
       <out>/gfx/event_pictures/<mod>_<slug>.png|dds
       <out>/interface/<mod>_event_pictures.gfx
           (only if a picture is an image path)
where <out> is mod_path from config.ini, or output/<mod>/ if it is not set.

Usage: modgen.py [project_folder]
The project folder holds config.ini and data/; it defaults to the folder
modgen.py is in.
"""

from __future__ import annotations

import configparser
import csv
import os
import re
import shutil
import struct
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

# A CSV row: the sheet's columns (all str) plus "_"-prefixed fields the
# validator adds (line number, parsed flags, sprite name, ...).
Row = dict[str, Any]
Options = defaultdict[str, list[Row]]

ROOT = Path(__file__).parent

EVENT_TYPES = {"country_event", "news_event", "state_event", "unit_leader_event"}
ID_RE = re.compile(r"^[A-Za-z0-9_]+\.\d+$")
# Lowercase, not all digits (so a leftover option number is caught), not
# t/d (the title/desc keys).
KEY_RE = re.compile(r"^(?!\d+$)[a-z0-9_]+$")
RESERVED_KEYS = {"t", "d"}
MOD_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")
ON_ACTION_RE = re.compile(r"^on_[a-z0-9_]+$")
# on_actions that run with no country in scope, so the event needs
# fire_scope to have a receiver.
SCOPELESS_ON_ACTIONS = {"on_startup"}
# Which kind of scope each event type runs in; firing across kinds needs a
# `scope`.
SCOPE_CLASS = {
    "country_event": "country",
    "news_event": "country",
    "state_event": "state",
    "unit_leader_event": "unit leader",
}
SCOPE_HINT = {
    "country": "owner",
    "state": "capital_scope",
    "unit leader": "a unit leader scope",
}
SCOPE_RE = re.compile(r"^[A-Za-z0-9_.:@]+$")
# A picture value is an image path (not a sprite name) if it has a / or \
# or ends in one of these.
IMAGE_EXTS = {".png", ".dds", ".tga", ".bmp", ".jpg", ".jpeg"}
# The formats copied as-is, with the bytes their files start with.
IMAGE_MAGIC = {".png": b"\x89PNG\r\n\x1a\n", ".dds": b"DDS "}
# Sizes Kaiserreich uses; other event types aren't checked because their
# sizes aren't known.
PICTURE_SIZE = {"country_event": (210, 176), "news_event": (397, 153)}
TRUE_WORDS = {"yes", "y", "true", "1"}
FALSE_WORDS = {"no", "n", "false", "0"}
GENERATED = "# GENERATED FILE - do not edit. Change the CSVs and re-run the generator."


# ----------------------------------------------------------------- helpers
def read_csv(data: Path, name: str, required: list[str]) -> list[Row]:
    """Read one CSV, skipping blank rows; each row gets its line number."""
    path = data / name
    if not path.exists():
        sys.exit(f"ERROR: missing file {path}")
    # utf-8-sig strips the BOM that Excel adds.
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = [c for c in required if c not in (reader.fieldnames or [])]
        if missing:
            columns = ", ".join(missing)
            sys.exit(f"ERROR: {name} is missing column(s): {columns}")
        rows = []
        # Line 1 is the header.
        for i, raw in enumerate(reader, start=2):
            row: Row = {k.strip(): (v or "").strip() for k, v in raw.items() if k}
            if not any(row.values()):
                continue
            row["_line"] = i
            rows.append(row)
    return rows


def parse_bool(value: str) -> bool | None:
    """Return None for '', True/False for yes/no words; else ValueError."""
    v = value.lower()
    if v == "":
        return None
    if v in TRUE_WORDS:
        return True
    if v in FALSE_WORDS:
        return False
    raise ValueError(value)


def brace_counts(line: str) -> tuple[int, int]:
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


def brace_problem(text: str) -> str | None:
    """Describe unbalanced braces in a raw script cell, or return None."""
    depth = 0
    for line in text.split("\n"):
        opens, closes = brace_counts(line)
        depth += opens - closes
        if depth < 0:
            return "has a } with no matching {"
    if depth > 0:
        return f"is missing {depth} closing }}"
    return None


def fmt_block(text: str, indent: int) -> list[str]:
    """Re-indent a raw script snippet using brace depth."""
    out = []
    depth = 0
    for raw in text.replace("\r", "").split("\n"):
        line = raw.strip()
        if not line:
            continue
        opens, closes = brace_counts(line)
        lead_close = len(line) - len(line.lstrip("}"))
        d = max(depth - lead_close, 0)
        out.append("\t" * (indent + d) + line)
        depth += opens - closes
    return out


def loc_escape(text: str) -> str:
    """Escape text for a localisation value."""
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


# -------------------------------------------------------------------- load
def load(data: Path) -> tuple[list[Row], list[Row], list[Row]]:
    """Read the three tables from the data folder."""
    events = read_csv(data, "events.csv", ["id", "title", "desc"])
    options = read_csv(data, "options.csv", ["event_id", "key", "name"])
    branches = read_csv(data, "branches.csv", ["from_event", "from_option", "to_event"])
    return events, options, branches


# ---------------------------------------------------------------- validate
def validate(
    events: list[Row], options: list[Row], branches: list[Row]
) -> tuple[list[str], list[str], dict[str, Row], Options]:
    """Check the tables; return errors, warnings, events and options."""
    errors: list[str] = []
    warnings: list[str] = []
    ev: dict[str, Row] = {}
    # Already reported; references to them are skipped quietly.
    bad_ids: set[str] = set()
    bad_keys: set[tuple[str, str]] = set()

    def check_script(loc: str, row: Row, cols: tuple[str, ...]) -> None:
        for col in cols:
            problem = row.get(col) and brace_problem(row[col])
            if problem:
                errors.append(f"{loc}: {col} {problem}")

    for e in events:
        eid = e["id"]
        loc = f"events.csv line {e['_line']}"
        if not ID_RE.match(eid):
            errors.append(
                f"{loc}: id '{eid}' must look like namespace.number (e.g. mymod.1)"
            )
            bad_ids.add(eid)
            continue
        if eid in ev:
            first = ev[eid]["_line"]
            errors.append(
                f"{loc}: duplicate event id '{eid}' (first used on line {first})"
            )
            continue
        etype = e.get("type") or "country_event"
        if etype not in EVENT_TYPES:
            types = ", ".join(sorted(EVENT_TYPES))
            errors.append(f"{loc}: type '{etype}' must be one of {types}")
        e["_type"] = etype

        flags: dict[str, bool | None] = {}
        for col in ("triggered_only", "fire_only_once", "hidden"):
            try:
                flags[col] = parse_bool(e.get(col, ""))
            except ValueError:
                value = e[col]
                errors.append(f"{loc}: {col} '{value}' must be yes, no, or blank")
                flags[col] = None
        e["_fire_only_once"] = bool(flags["fire_only_once"])
        e["_hidden"] = bool(flags["hidden"])
        # Nobody sees a hidden event, so it needs no text.
        if not e["title"] and not e["_hidden"]:
            errors.append(f"{loc}: event '{eid}' has no title")
        if not e["desc"] and not e["_hidden"]:
            warnings.append(f"{loc}: event '{eid}' has no description")

        # fired_by decides how the event starts; triggered_only must agree.
        triggered = flags["triggered_only"]
        fired_by = e.get("fired_by", "")
        fire_scope = e.get("fire_scope", "")
        mtth = e.get("mtth_days", "")
        if fired_by == "mtth":
            if triggered:
                errors.append(
                    f"{loc}: fired_by = mtth means the event fires on its own; "
                    "set triggered_only to no or leave it blank"
                )
            triggered = False
            if not mtth:
                errors.append(f"{loc}: fired_by = mtth needs mtth_days")
        elif ON_ACTION_RE.match(fired_by):
            if triggered is False:
                errors.append(
                    f"{loc}: fired_by = {fired_by} fires the event directly; "
                    "set triggered_only to yes or leave it blank"
                )
            triggered = True
            if fired_by in SCOPELESS_ON_ACTIONS and not fire_scope:
                errors.append(
                    f"{loc}: {fired_by} has no country in scope; set fire_scope "
                    "to who gets the event (e.g. GER, or every_country and "
                    "limit it with trigger)"
                )
            elif etype not in ("country_event", "news_event") and not fire_scope:
                warnings.append(
                    f"{loc}: {etype} '{eid}' is fired from {fired_by}; check "
                    "that on_action runs in the right scope"
                )
        elif fired_by not in ("", "external"):
            errors.append(
                f"{loc}: fired_by '{fired_by}' must be blank, external, mtth, or "
                "an on_action such as on_startup"
            )
        if triggered is None:
            triggered = True
        if mtth:
            if not re.fullmatch(r"[1-9]\d*", mtth):
                errors.append(
                    f"{loc}: mtth_days '{mtth}' must be a whole number of days"
                )
            elif triggered:
                errors.append(
                    f"{loc}: mtth_days does nothing on a triggered-only event; "
                    "set fired_by = mtth"
                )
        e["_triggered_only"] = triggered
        e["_fired_by"] = fired_by
        if fire_scope and not SCOPE_RE.match(fire_scope):
            errors.append(
                f"{loc}: fire_scope '{fire_scope}' must be a single scope, e.g. "
                "GER or every_country"
            )
        elif fire_scope and not ON_ACTION_RE.match(fired_by):
            errors.append(
                f"{loc}: fire_scope only applies when fired_by is an on_action"
            )
        check_script(loc, e, ("trigger", "immediate", "extra"))
        ev[eid] = e

    opts: Options = defaultdict(list)
    for o in options:
        event_id = o["event_id"]
        key = o["key"]
        order = o.get("order", "")
        loc = f"options.csv line {o['_line']}"
        if event_id in bad_ids:
            continue
        if event_id not in ev:
            errors.append(f"{loc}: event_id '{event_id}' does not exist in events.csv")
            continue
        if not KEY_RE.match(key) or key in RESERVED_KEYS:
            errors.append(
                f"{loc}: key '{key}' must be lowercase letters, digits and _, "
                "not only digits, and not 't' or 'd' (e.g. mobilize)"
            )
            bad_keys.add((event_id, key))
            continue
        if any(other["key"] == key for other in opts[event_id]):
            errors.append(
                f"{loc}: event '{event_id}' already has an option with key '{key}'"
            )
            continue
        if not o["name"] and not ev[event_id]["_hidden"]:
            errors.append(f"{loc}: option '{key}' of '{event_id}' has no name")
        if order and not re.fullmatch(r"-?\d+", order):
            errors.append(f"{loc}: order '{order}' must be a whole number")
            # Already reported; keep the option so later checks don't cascade.
            o["_bad_order"] = True
        check_script(loc, o, ("effects", "trigger", "extra"))
        if o.get("ai_chance") and not re.fullmatch(r"-?\d+(\.\d+)?", o["ai_chance"]):
            check_script(loc, o, ("ai_chance",))
        opts[event_id].append(o)

    for eid, olist in opts.items():
        if any(o.get("_bad_order") for o in olist):
            continue
        with_order = [o for o in olist if o.get("order")]
        if with_order and len(with_order) != len(olist):
            errors.append(
                f"options.csv: event '{eid}' has an order on some options but "
                "not all; fill in every row or none"
            )
        elif with_order:
            # sort() is stable, so ties keep row order.
            olist.sort(key=lambda o: int(o["order"]))

    for eid, e in ev.items():
        line = e["_line"]
        count = len(opts[eid])
        if not count and not e["_hidden"]:
            errors.append(
                f"events.csv line {line}: event '{eid}' has no options in options.csv"
            )
        if e["_hidden"] and count > 1:
            warnings.append(
                f"events.csv line {line}: hidden event '{eid}' has {count} "
                "options; nobody sees the choice"
            )

    incoming: set[str] = set()
    for b in branches:
        src = b["from_event"]
        to = b["to_event"]
        option = b["from_option"]
        loc = f"branches.csv line {b['_line']}"
        if src in bad_ids or to in bad_ids or (src, option) in bad_keys:
            continue
        if src not in ev:
            errors.append(f"{loc}: from_event '{src}' does not exist")
            continue
        if to not in ev:
            errors.append(
                f"{loc}: to_event '{to}' does not exist (fired from '{src}' "
                f"option {option})"
            )
            continue
        keys = [o["key"] for o in opts[src]]
        if option not in keys:
            hint = ""
            if option.isdigit():
                hint = " (from_option takes the option's key, not its number)"
            listed = ", ".join(keys) or "none"
            errors.append(
                f"{loc}: from_option '{option}' is not an option key of "
                f"'{src}'{hint}; its keys are: {listed}"
            )
            continue
        for col in ("days", "hours", "random_days"):
            value = b.get(col, "")
            if value and not re.fullmatch(r"\d+", value):
                errors.append(f"{loc}: {col} '{value}' must be a whole number")
        try:
            b["_hidden"] = bool(parse_bool(b.get("hidden", "")))
        except ValueError:
            value = b["hidden"]
            errors.append(f"{loc}: hidden '{value}' must be yes, no, or blank")
        scope = b.get("scope", "")
        src_type = ev[src]["_type"]
        to_type = ev[to]["_type"]
        src_class = SCOPE_CLASS.get(src_type)
        to_class = SCOPE_CLASS.get(to_type)
        if scope and not SCOPE_RE.match(scope):
            errors.append(
                f"{loc}: scope '{scope}' must be a single scope, e.g. "
                "capital_scope, GER, 64"
            )
        elif not scope and src_class and to_class and src_class != to_class:
            hint = SCOPE_HINT[to_class]
            errors.append(
                f"{loc}: '{to}' is a {to_type} but '{src}' is a {src_type}; "
                f"set scope to say which {to_class} gets it (e.g. {hint})"
            )
        check_script(loc, b, ("condition",))
        incoming.add(to)

    for eid, e in ev.items():
        if e["_triggered_only"] and not e["_fired_by"] and eid not in incoming:
            warnings.append(
                f"event '{eid}' is triggered-only but nothing fires it; set "
                "fired_by (e.g. on_startup, or external if a focus/decision "
                "fires it)"
            )
    return errors, warnings, ev, opts


# ---------------------------------------------------------------- generate
def gen_events(ev: dict[str, Row], opts: Options, branches: list[Row]) -> str:
    """Build the event script file."""
    # event id -> option key -> branches
    out_by: defaultdict[str, defaultdict[str, list[Row]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for b in branches:
        out_by[b["from_event"]][b["from_option"]].append(b)

    namespaces: list[str] = []
    for eid in ev:
        ns = eid.split(".")[0]
        if ns not in namespaces:
            namespaces.append(ns)

    lines = [GENERATED, ""]
    lines += [f"add_namespace = {ns}" for ns in namespaces]
    lines.append("")

    for eid, e in ev.items():
        etype = e["_type"]
        lines.append(f"{etype} = {{")
        lines.append(f"\tid = {eid}")
        if e["title"]:
            lines.append(f"\ttitle = {eid}.t")
        if e["desc"]:
            lines.append(f"\tdesc = {eid}.d")
        if e.get("picture"):
            picture = e.get("_sprite") or e["picture"]
            lines.append(f"\tpicture = {picture}")
        if e["_hidden"]:
            lines.append("\thidden = yes")
        if e["_triggered_only"]:
            lines.append("\tis_triggered_only = yes")
        if e["_fire_only_once"]:
            lines.append("\tfire_only_once = yes")
        if e.get("mtth_days"):
            days = e["mtth_days"]
            lines.append(f"\tmean_time_to_happen = {{ days = {days} }}")
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
            key = o["key"]
            lines.append("\toption = {")
            if o["name"]:
                lines.append(f"\t\tname = {eid}.{key}")
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
            for b in out_by[eid].get(key, []):
                to = b["to_event"]
                target_type = ev[to]["_type"]
                parts = [f"id = {to}"]
                for col in ("days", "hours", "random_days"):
                    if b.get(col):
                        value = b[col]
                        parts.append(f"{col} = {value}")
                body = " ".join(parts)
                call = f"{target_type} = {{ {body} }}"
                if b.get("scope"):
                    scope = b["scope"]
                    call = f"{scope} = {{ {call} }}"
                if b.get("_hidden"):
                    # No "fires in N days" tooltip.
                    call = f"hidden_effect = {{ {call} }}"
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


def gen_on_actions(ev: dict[str, Row]) -> str | None:
    """Build the on_actions file, or None if no event uses an on_action."""
    by_action: defaultdict[str, list[str]] = defaultdict(list)
    for eid, e in ev.items():
        fired_by = e["_fired_by"]
        if ON_ACTION_RE.match(fired_by):
            etype = e["_type"]
            call = f"{etype} = {{ id = {eid} }}"
            if e.get("fire_scope"):
                fire_scope = e["fire_scope"]
                call = f"{fire_scope} = {{ {call} }}"
            by_action[fired_by].append(call)
    if not by_action:
        return None
    lines = [GENERATED, "", "on_actions = {"]
    for action, calls in by_action.items():
        lines += [f"\t{action} = {{", "\t\teffect = {"]
        lines += [f"\t\t\t{c}" for c in calls]
        lines += ["\t\t}", "\t}"]
    lines.append("}")
    return "\n".join(lines) + "\n"


def gen_loc(ev: dict[str, Row], opts: Options) -> str:
    """Build the English localisation file."""
    lines = ["l_english:"]
    for eid, e in ev.items():
        if e["title"]:
            title = loc_escape(e["title"])
            lines.append(f' {eid}.t:0 "{title}"')
        if e["desc"]:
            desc = loc_escape(e["desc"])
            lines.append(f' {eid}.d:0 "{desc}"')
        for o in opts[eid]:
            if o["name"]:
                key = o["key"]
                name = loc_escape(o["name"])
                lines.append(f' {eid}.{key}:0 "{name}"')
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- pictures
def is_picture_path(value: str) -> bool:
    """Return True if a picture value is an image path, not a sprite name."""
    value = value.replace("\\", "/")
    return "/" in value or Path(value).suffix.lower() in IMAGE_EXTS


def image_size(head: bytes, ext: str) -> tuple[int, int] | None:
    """Read (width, height) from the first 24 bytes of a PNG or DDS."""
    if len(head) < 24:
        return None
    if ext == ".png":
        width, height = struct.unpack(">II", head[16:24])
    else:
        height, width = struct.unpack("<II", head[12:20])
    return width, height


def check_pictures(
    ev: dict[str, Row], project: Path, mod_name: str
) -> tuple[list[str], list[str], dict[str, Path]]:
    """Resolve image paths in the picture column.

    Return errors, warnings and {slug: source file}, and set e["_sprite"]
    on events that use an image.
    """
    errors: list[str] = []
    warnings: list[str] = []
    images: dict[str, Path] = {}
    first_line: dict[str, int] = {}
    for e in ev.values():
        value = e.get("picture", "")
        if not value or not is_picture_path(value):
            continue
        loc = f"events.csv line {e['_line']}"
        src = (project / value.replace("\\", "/")).resolve()
        try:
            rel = src.relative_to(project)
        except ValueError:
            errors.append(
                f"{loc}: picture '{value}' is outside the project folder; put "
                "images inside it (the sprite name and the copy are built from "
                "the path relative to it)"
            )
            continue
        if not src.is_file():
            errors.append(
                f"{loc}: picture file '{value}' not found (paths are relative "
                f"to {project})"
            )
            continue
        ext = src.suffix.lower()
        if ext not in IMAGE_MAGIC:
            errors.append(f"{loc}: picture '{value}' must be a .png or .dds file")
            continue
        with open(src, "rb") as f:
            head = f.read(24)
        if not head.startswith(IMAGE_MAGIC[ext]):
            errors.append(
                f"{loc}: picture '{value}' is not really a {ext} file (was it renamed?)"
            )
            continue
        stem = rel.with_suffix("").as_posix().lower()
        slug = re.sub(r"[^a-z0-9]+", "_", stem).strip("_")
        if not slug:
            errors.append(
                f"{loc}: picture '{value}' needs a file name with letters or "
                "digits in it"
            )
            continue
        if slug in images and not os.path.samefile(images[slug], src):
            first = first_line[slug]
            errors.append(
                f"{loc}: picture '{value}' and the one on line {first} would "
                f"both be named {slug}; rename one of the files"
            )
            continue
        images.setdefault(slug, src)
        first_line.setdefault(slug, e["_line"])
        etype = e["_type"]
        expected = PICTURE_SIZE.get(etype)
        size = image_size(head, ext)
        if expected and size and size != expected:
            width, height = size
            want_width, want_height = expected
            warnings.append(
                f"{loc}: picture '{value}' is {width}x{height}; {etype} pictures "
                f"are usually {want_width}x{want_height}"
            )
        e["_sprite"] = f"GFX_{mod_name}_{slug}"
    return errors, warnings, images


def gen_gfx(images: dict[str, Path], mod_name: str) -> str:
    """Build the interface .gfx file registering each image as a sprite."""
    lines = [GENERATED, "", "spriteTypes = {"]
    for slug, src in images.items():
        ext = src.suffix.lower()
        lines += [
            "\tspriteType = {",
            f'\t\tname = "GFX_{mod_name}_{slug}"',
            f'\t\ttexturefile = "gfx/event_pictures/{mod_name}_{slug}{ext}"',
            "\t}",
        ]
    lines.append("}")
    return "\n".join(lines) + "\n"


def write_pictures(out: Path, images: dict[str, Path], mod_name: str) -> None:
    """Copy images, remove unused ones an earlier run wrote, write the .gfx.

    The .gfx is written last because it lists the files the generator owns.
    """
    pic_dir = out / "gfx" / "event_pictures"
    gfx_file = out / "interface" / f"{mod_name}_event_pictures.gfx"
    wanted: set[str] = set()
    for slug, src in images.items():
        ext = src.suffix.lower()
        dest = pic_dir / f"{mod_name}_{slug}{ext}"
        wanted.add(dest.name)
        if not (dest.exists() and dest.read_bytes() == src.read_bytes()):
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)

    old = gfx_file.read_text(encoding="utf-8") if gfx_file.exists() else ""
    if old.startswith(GENERATED):
        for texture in re.findall(r'texturefile\s*=\s*"([^"]+)"', old):
            stale = (out / texture).resolve()
            # Only files this generator could have written:
            # <out>/gfx/event_pictures/<mod>_*
            if (
                stale.parent == pic_dir.resolve()
                and stale.name.startswith(f"{mod_name}_")
                and stale.name not in wanted
                and stale.is_file()
            ):
                stale.unlink()

    if images:
        write(gfx_file, gen_gfx(images, mod_name))
    elif old.startswith(GENERATED):
        gfx_file.unlink()


# -------------------------------------------------------------------- main
def write(path: Path, text: str, bom: bool = False) -> None:
    """Write a text file, creating its folder; HOI4 loc files need a BOM."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8-sig" if bom else "utf-8")


def main() -> None:
    """Validate the project's CSVs and write the generated mod files."""
    cfg = configparser.ConfigParser()
    # Resolved, so image paths (also resolved) compare correctly through
    # symlinks and mapped drives.
    project = (Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT).resolve()
    if not (project / "config.ini").is_file():
        sys.exit(f"ERROR: no config.ini in {project}")
    cfg.read(project / "config.ini", encoding="utf-8")
    mod_name = cfg.get("mod", "name", fallback="my_mod").strip()
    if not MOD_NAME_RE.match(mod_name):
        sys.exit(
            f"ERROR: config.ini name '{mod_name}' must be letters, digits and _ "
            "only (it is used in file names)"
        )
    mod_path = cfg.get("mod", "mod_path", fallback="").strip()

    events, options, branches = load(project / "data")
    errors, warnings, ev, opts = validate(events, options, branches)
    images: dict[str, Path] = {}
    # Image checks need valid events.
    if not errors:
        pic_errors, pic_warnings, images = check_pictures(ev, project, mod_name)
        errors += pic_errors
        warnings += pic_warnings

    for w in warnings:
        print("WARNING:", w)
    if errors:
        print()
        for err in errors:
            print("ERROR:", err)
        count = len(errors)
        sys.exit(
            f"\n{count} error(s) found. Nothing was generated. Fix the CSVs and "
            "run again."
        )

    if mod_path:
        out = Path(mod_path).expanduser()
        if not out.is_absolute():
            out = project / out
        if not out.is_dir():
            sys.exit(
                f"ERROR: config.ini mod_path '{mod_path}' is not an existing folder"
            )
    else:
        out = project / "output" / mod_name

    # Only ever touch files named after this mod, so other files in the mod
    # folder are safe.
    write(out / "events" / f"{mod_name}_events.txt", gen_events(ev, opts, branches))
    # HOI4 localisation must be UTF-8 WITH BOM, and the name must end in
    # _l_english.yml.
    loc_file = out / "localisation" / "english" / f"{mod_name}_events_l_english.yml"
    write(loc_file, gen_loc(ev, opts), bom=True)
    on_actions_file = out / "common" / "on_actions" / f"{mod_name}_on_actions.txt"
    on_actions = gen_on_actions(ev)
    if on_actions:
        write(on_actions_file, on_actions)
    elif on_actions_file.exists():
        if on_actions_file.read_text(encoding="utf-8").startswith(GENERATED):
            # Stale from an earlier run; it would still fire old events.
            on_actions_file.unlink()
    write_pictures(out, images, mod_name)
    event_count = len(ev)
    option_count = sum(len(v) for v in opts.values())
    print(f"\nOK: {event_count} events, {option_count} options written to {out}")


if __name__ == "__main__":
    main()
