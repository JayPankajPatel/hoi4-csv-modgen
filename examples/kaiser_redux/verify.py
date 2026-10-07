#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Check that the generated example matches the Kaiserredux originals.

Downloads the original event and localisation files at a pinned
Kaiserredux commit, then compares them with output/kx_example/ by
meaning, not by text:

- Event- and option-level fields are compared ignoring order (the game
  reads them by name). Options keep their order, and effects inside
  options and every nested block keep theirs.
- Comments and whitespace are ignored. `country_event = x.1` equals
  `country_event = { id = x.1 }`.
- Localisation: every generated key must have the original's text.
  `\\"` and a bare `"` inside text are treated as equal (Kaiserredux
  uses both).

Run from the repository root, after generating:
    uv run modgen.py examples/kaiser_redux
    uv run examples/kaiser_redux/verify.py
"""

import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

KX_COMMIT = "30dffc2614130cf3b57dd77d618e1795df742e5c"
RAW = f"https://raw.githubusercontent.com/JoeBidenWhatAreYouHiding/kx/{KX_COMMIT}/"
EVENT_FILES = [
    "Kachin.txt",
    "city_capture_small.txt",
    "Sierra_Leone.txt",
    "Ghana.txt",
    "Flavor News.txt",
    "Transylvania.txt",
    "Kurdistan.txt",
    "Unit_Leaders.txt",
]
LOC_FILES = [
    "KR_Kachin_l_english.yml",
    "KX_City_Capture_Small_l_english.yml",
    "KR_Sierra_Leone_l_english.yml",
    "FA_Ghana_l_english.yml",
    "00_Flavor_News_l_english.yml",
    "KR_Transylvania_l_english.yml",
    "KR_Kurdistan_l_english.yml",
    "KR_Leaders_l_english.yml",
]
# Kaiserredux has no localisation for these; the example fills them with
# TODO
KNOWN_MISSING_LOC = {
    f"flavornews.{n}.{k}" for n in (3, 4, 5) for k in ("t", "d", "a")
}

HERE = Path(__file__).parent
CACHE = HERE / ".cache" / KX_COMMIT[:12]
OUT = HERE / "output" / "kx_example"

TOKEN_RE = re.compile(
    r'"(?:[^"\\]|\\.)*"|[{}]|<=|>=|!=|[=<>]|[^\s{}=<>#"]+|#[^\n]*'
)
OPERATORS = ("=", "<", ">", "<=", ">=", "!=")
EVENT_TYPES = {
    "country_event",
    "news_event",
    "state_event",
    "unit_leader_event",
    "operative_leader_event",
}
OPTION_UNORDERED = {
    "name",
    "trigger",
    "ai_chance",
    "original_recipient_only",
    "highlight_states",
}
BOOL_FLAGS = {
    "is_triggered_only",
    "fire_only_once",
    "hidden",
    "major",
    "fire_for_sender",
}
LOC_RE = re.compile(r'^\s*([\w.\-]+):\d*\s*"(.*)"\s*(#.*)?$')


def fetch(folder, name):
    path = CACHE / folder / name
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        url = RAW + folder + "/" + urllib.parse.quote(name)
        with urllib.request.urlopen(url) as r:
            path.write_bytes(r.read())
    return path.read_text(encoding="utf-8-sig")


# --------------------------------------------------------------- script
def parse(tokens, i=0, top=True):
    items = []
    while i < len(tokens):
        t = tokens[i]
        if t == "}":
            if top:
                raise SyntaxError("unbalanced }")
            return items, i + 1
        if i + 1 < len(tokens) and tokens[i + 1] in OPERATORS:
            if tokens[i + 2] == "{":
                val, j = parse(tokens, i + 3, top=False)
            else:
                val, j = tokens[i + 2], i + 3
            items.append((t, tokens[i + 1], val))
            i = j
        elif t == "{":
            val, i = parse(tokens, i + 1, top=False)
            items.append(("", "", val))
        else:
            items.append(("", "", t))
            i += 1
    if not top:
        raise SyntaxError("missing }")
    return items, i


def canon(v):
    return (
        v
        if isinstance(v, str)
        else tuple(canon_item(k, o, x) for k, o, x in v)
    )


def canon_item(k, o, v):
    # shorthand for { id = ... }
    if k in EVENT_TYPES and isinstance(v, str):
        return (k, o, (("id", "=", v),))
    return (k, o, canon(v))


def canon_event(items):
    fields = sorted(
        canon_item(k, o, v)
        for k, o, v in items
        if k != "option" and not (k in BOOL_FLAGS and v == "no")
    )
    options = []
    for k, _, v in items:
        if k == "option":
            fixed = sorted(
                canon_item(kk, o, x)
                for kk, o, x in v
                if kk in OPTION_UNORDERED
            )
            effects = tuple(
                canon_item(kk, o, x)
                for kk, o, x in v
                if kk not in OPTION_UNORDERED
            )
            options.append((tuple(fixed), effects))
    return tuple(fields), options


def events_of(text):
    tokens = [t for t in TOKEN_RE.findall(text) if not t.startswith("#")]
    items, _ = parse(tokens)
    out = {}
    for k, _, v in items:
        if k in EVENT_TYPES:
            eid = next(x for kk, _, x in v if kk == "id")
            out[eid] = (k, canon_event(v))
    return out


def show(x):
    if isinstance(x, str):
        return x
    return (
        "{ " + " ".join(f"{k} {o} {show(v)}".strip() for k, o, v in x) + " }"
    )


# --------------------------------------------------------- localisation
def loc_of(text):
    out = {}
    for line in text.splitlines():
        m = LOC_RE.match(line)
        if m:
            out[m.group(1)] = m.group(2).replace('\\"', '"')
    return out


def main():
    gen_events_file = OUT / "events" / "kx_example_events.txt"
    gen_loc_file = (
        OUT / "localisation" / "english" / "kx_example_events_l_english.yml"
    )
    if not gen_events_file.exists():
        sys.exit("Generate first: uv run modgen.py examples/kaiser_redux")

    original = {}
    for name in EVENT_FILES:
        original.update(events_of(fetch("events", name)))
    generated = events_of(gen_events_file.read_text(encoding="utf-8-sig"))

    problems = 0
    for eid in sorted(set(original) | set(generated)):
        if eid not in generated or eid not in original:
            print(
                f"{eid}: only in "
                f"{'original' if eid in original else 'generated'}"
            )
            problems += 1
            continue
        (ta, (fa, oa)), (tb, (fb, ob)) = original[eid], generated[eid]
        if ta != tb:
            print(f"{eid}: type {ta} vs {tb}")
            problems += 1
        if fa != fb:
            problems += 1
            print(f"{eid}: event fields differ")
            for x in sorted(set(fa) - set(fb)):
                print(f"   original:  {show((x,))}")
            for x in sorted(set(fb) - set(fa)):
                print(f"   generated: {show((x,))}")
        if len(oa) != len(ob):
            print(f"{eid}: {len(oa)} options vs {len(ob)}")
            problems += 1
        for n, (x, y) in enumerate(zip(oa, ob), 1):
            if x != y:
                problems += 1
                print(f"{eid}: option {n} differs")
                print(f"   original:  {show((('', '', x[0] + x[1]),))}")
                print(f"   generated: {show((('', '', y[0] + y[1]),))}")
    print(f"script: {len(original)} events compared, {problems} difference(s)")

    orig_loc = {}
    for name in LOC_FILES:
        orig_loc.update(loc_of(fetch("localisation", name)))
    gen_loc = loc_of(gen_loc_file.read_text(encoding="utf-8-sig"))
    loc_problems = 0
    for k, v in gen_loc.items():
        if k in KNOWN_MISSING_LOC:
            continue
        if orig_loc.get(k) != v:
            loc_problems += 1
            print(
                f"{k}:\n   original:  {orig_loc.get(k, '<missing>')}\n   "
                f"generated: {v}"
            )
    checked = len(gen_loc) - len(KNOWN_MISSING_LOC & set(gen_loc))
    has_bom = gen_loc_file.read_bytes()[:3] == b"\xef\xbb\xbf"
    print(
        f"localisation: {checked} keys compared, {loc_problems} "
        f"difference(s), BOM {'ok' if has_bom else 'MISSING'}"
    )
    return 1 if problems or loc_problems or not has_bom else 0


if __name__ == "__main__":
    sys.exit(main())
