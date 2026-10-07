"""Tests for modgen.py.

Golden tests run the generator end to end on a fixture project and compare
its exit code, every message it prints and every file it writes with the
recorded copy in tests/golden/<case>/. The expected files live in files/,
not output/, because .gitignore ignores every output/ folder.

After an intended change, re-record with:

    UPDATE_GOLDEN=1 uv run python -m unittest discover -s tests

and review the diff of tests/golden/ before committing.
"""

from __future__ import annotations

import csv
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "tests" / "fixtures"
GOLDEN = REPO / "tests" / "golden"
UPDATE = os.environ.get("UPDATE_GOLDEN") == "1"
TEXT_SUFFIXES = {".txt", ".yml", ".gfx"}

sys.path.insert(0, str(REPO))
import modgen  # noqa: E402


# ----------------------------------------------------------------- helpers
def make_png(path: Path, width: int, height: int) -> None:
    """Write a solid-colour RGB PNG using only the standard library."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = struct.pack(">I", zlib.crc32(kind + data))
        return struct.pack(">I", len(data)) + kind + data + crc

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x5c\x4a\x3a" * width for _ in range(height))
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


def make_dds(path: Path, width: int, height: int) -> None:
    """Write the first 24 bytes of a DDS file (enough for the size check)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"DDS " + struct.pack("<IIIII", 124, 0x1007, height, width, 0))


def set_pictures(project: Path, pictures: list[str]) -> None:
    """Set the picture column of events.csv, one value per row."""
    events = project / "data" / "events.csv"
    with open(events, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    for row, picture in zip(rows, pictures):
        row["picture"] = picture
    with open(events, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def run_modgen(project: Path) -> tuple[int, str]:
    """Run the generator on a project; return its exit code and output.

    The project's path is replaced with <project> (with / separators) so
    output is the same on every machine and OS.
    """
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    result = subprocess.run(
        [sys.executable, str(REPO / "modgen.py"), str(project)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )
    output = result.stdout + result.stderr
    pattern = re.escape(str(project)) + r"[^\s)']*"

    def relative(match: re.Match[str]) -> str:
        tail = match.group(0)[len(str(project)) :]
        return "<project>" + tail.replace("\\", "/")

    return result.returncode, re.sub(pattern, relative, output)


def read_tree(root: Path) -> dict[str, bytes]:
    """Map each file under root (relative, / separators) to its bytes.

    Line endings in text files are normalised, because Path.write_text
    writes CRLF on Windows.
    """
    tree: dict[str, bytes] = {}
    if not root.is_dir():
        return tree
    for path in sorted(root.rglob("*")):
        if path.is_file():
            data = path.read_bytes()
            if path.suffix in TEXT_SUFFIXES:
                data = data.replace(b"\r\n", b"\n")
            tree[path.relative_to(root).as_posix()] = data
    return tree


class GoldenTestCase(unittest.TestCase):
    """Run the generator on a temporary copy of a fixture project."""

    maxDiff = None

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        # Resolved, so it matches the path modgen prints (macOS /private).
        self.project = Path(tmp.name).resolve() / "project"

    def copy_fixture(self, name: str) -> None:
        shutil.copytree(FIXTURES / name, self.project)

    def assertGolden(self, case: str) -> None:
        """Run the generator and compare with tests/golden/<case>/."""
        code, output = run_modgen(self.project)
        stdout = f"exit code: {code}\n{output}"
        files = read_tree(self.project / "output")
        golden = GOLDEN / case
        if UPDATE:
            shutil.rmtree(golden, ignore_errors=True)
            golden.mkdir(parents=True)
            (golden / "stdout.txt").write_text(stdout, encoding="utf-8", newline="\n")
            for rel, data in files.items():
                dest = golden / "files" / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
            return
        if not golden.is_dir():
            self.fail(f"no golden output for {case}; run with UPDATE_GOLDEN=1")
        expected = (golden / "stdout.txt").read_bytes().replace(b"\r\n", b"\n")
        self.assertEqual(stdout, expected.decode("utf-8"))
        self.assertEqual(files, read_tree(golden / "files"))


# ------------------------------------------------------------ golden tests
class SampleTests(GoldenTestCase):
    def test_sample_project(self) -> None:
        """The repository's sample project generates the recorded mod."""
        shutil.copytree(REPO / "data", self.project / "data")
        shutil.copytree(REPO / "pictures", self.project / "pictures")
        shutil.copy(REPO / "config.ini", self.project / "config.ini")
        self.assertGolden("sample")


class ValidationTests(GoldenTestCase):
    def test_every_csv_error_and_warning(self) -> None:
        """Broken CSVs report each problem once and write nothing."""
        self.copy_fixture("bad")
        self.assertGolden("bad_csvs")


class ConfigTests(GoldenTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.copy_fixture("pictures")
        make_png(self.project / "pictures" / "crisis.png", 210, 176)

    def write_config(self, text: str) -> None:
        (self.project / "config.ini").write_text(text, encoding="utf-8")

    def test_invalid_mod_name(self) -> None:
        """A mod name that can't be part of a file name is rejected."""
        self.write_config("[mod]\nname = bad name\n")
        self.assertGolden("config_bad_name")

    def test_missing_mod_path(self) -> None:
        """A mod_path that isn't an existing folder is rejected."""
        self.write_config("[mod]\nname = ok\nmod_path = nowhere\n")
        self.assertGolden("config_missing_mod_path")

    def test_missing_config(self) -> None:
        """A project folder without config.ini is rejected."""
        (self.project / "config.ini").unlink()
        self.assertGolden("config_missing")


class PictureTests(GoldenTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.copy_fixture("pictures")
        pictures = self.project / "pictures"
        make_png(pictures / "war.png", 210, 176)
        make_dds(pictures / "war.dds", 16, 16)
        make_dds(pictures / "small.dds", 16, 16)
        (pictures / "fake.png").write_bytes(b"\xff\xd8\xff")
        (pictures / "a.jpg").write_bytes(b"x")
        make_png(self.project / "ü.png", 210, 176)

    def test_missing_outside_and_unsupported(self) -> None:
        """Missing, outside-the-project and unsupported pictures are errors."""
        set_pictures(
            self.project, ["pictures/missing.png", "../outside.png", "pictures/a.jpg"]
        )
        self.assertGolden("pictures_missing_outside_unsupported")

    def test_renamed_file_and_name_collision(self) -> None:
        """A renamed file and two files with one sprite name are errors."""
        set_pictures(
            self.project, ["pictures/fake.png", "pictures/war.png", "pictures/war.dds"]
        )
        self.assertGolden("pictures_renamed_and_collision")

    def test_file_name_without_letters(self) -> None:
        """A file name with no letters or digits can't become a sprite name."""
        set_pictures(self.project, ["ü.png", "GFX_y", "GFX_x"])
        self.assertGolden("pictures_empty_name")

    def test_shared_backslash_and_size_warning(self) -> None:
        """Backslash paths share one sprite; a wrong size warns and builds."""
        set_pictures(
            self.project,
            ["pictures/war.png", "pictures/small.dds", "pictures\\war.png"],
        )
        self.assertGolden("pictures_shared_and_size_warning")


# --------------------------------------------------------- behaviour tests
class StalePictureTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.project = Path(tmp.name).resolve() / "project"
        shutil.copytree(FIXTURES / "pictures", self.project)
        make_png(self.project / "pictures" / "war.png", 210, 176)
        self.out = self.project / "output" / "my_mod"
        self.gfx = self.out / "interface" / "my_mod_event_pictures.gfx"
        self.copy = self.out / "gfx" / "event_pictures" / "my_mod_pictures_war.png"

    def test_unused_picture_is_removed_and_other_files_are_kept(self) -> None:
        """Dropping a picture deletes only the files the generator wrote."""
        set_pictures(self.project, ["pictures/war.png", "GFX_a", "GFX_b"])
        self.assertEqual(run_modgen(self.project)[0], 0)
        self.assertTrue(self.copy.is_file())
        self.assertTrue(self.gfx.is_file())
        foreign_image = self.copy.with_name("someone_else.png")
        foreign_image.write_bytes(b"keep")
        foreign_gfx = self.gfx.with_name("other.gfx")
        foreign_gfx.write_text("keep", encoding="utf-8")
        # A hand-edited entry must not make the generator delete other files.
        events_file = self.out / "events" / "my_mod_events.txt"
        evil = (
            '\tspriteType = { name = "GFX_x" texturefile = '
            '"gfx/event_pictures/../../events/my_mod_events.txt" }\n}\n'
        )
        text = self.gfx.read_text(encoding="utf-8").rstrip().removesuffix("}")
        self.gfx.write_text(text + evil, encoding="utf-8")

        set_pictures(self.project, ["GFX_a", "GFX_b", "GFX_c"])
        self.assertEqual(run_modgen(self.project)[0], 0)
        self.assertFalse(self.copy.exists())
        self.assertFalse(self.gfx.exists())
        self.assertTrue(foreign_image.is_file())
        self.assertTrue(foreign_gfx.is_file())
        self.assertTrue(events_file.is_file())

    def test_hand_written_gfx_is_not_deleted(self) -> None:
        """A .gfx without the generated header is never removed."""
        set_pictures(self.project, ["GFX_a", "GFX_b", "GFX_c"])
        self.gfx.parent.mkdir(parents=True)
        self.gfx.write_text("spriteTypes = { }\n", encoding="utf-8")
        self.assertEqual(run_modgen(self.project)[0], 0)
        self.assertTrue(self.gfx.is_file())

    @unittest.skipIf(sys.platform == "win32", "symlinks need privileges on Windows")
    def test_symlinked_project_folder(self) -> None:
        """Pictures resolve when the project is reached through a symlink."""
        set_pictures(self.project, ["pictures/war.png", "GFX_a", "GFX_b"])
        link = self.project.parent / "link"
        link.symlink_to(self.project, target_is_directory=True)
        code, output = run_modgen(link)
        self.assertEqual(code, 0, output)
        self.assertTrue(self.copy.is_file())


# -------------------------------------------------------------- unit tests
class HelperTests(unittest.TestCase):
    def test_parse_bool(self) -> None:
        """Yes/no words parse; blank is None; anything else is an error."""
        for word in ("yes", "Y", "true", "1"):
            self.assertIs(modgen.parse_bool(word), True)
        for word in ("no", "N", "false", "0"):
            self.assertIs(modgen.parse_bool(word), False)
        self.assertIsNone(modgen.parse_bool(""))
        with self.assertRaises(ValueError):
            modgen.parse_bool("maybe")

    def test_brace_problem_ignores_comments_and_strings(self) -> None:
        """Braces in # comments and quoted strings don't count."""
        text = 'if = {\n\tlimit = { a = b } # close } here\n\tx = "odd { y"\n}'
        self.assertIsNone(modgen.brace_problem(text))
        self.assertEqual(modgen.brace_problem("a = {"), "is missing 1 closing }")
        self.assertEqual(modgen.brace_problem("}"), "has a } with no matching {")

    def test_fmt_block_reindents_by_depth(self) -> None:
        """Script is re-indented from brace depth, starting at indent."""
        lines = modgen.fmt_block("if = {\nlimit = { x = y }\n  a = b\n}", 1)
        self.assertEqual(
            lines, ["\tif = {", "\t\tlimit = { x = y }", "\t\ta = b", "\t}"]
        )

    def test_loc_escape(self) -> None:
        """Quotes, backslashes and newlines are escaped for HOI4."""
        self.assertEqual(modgen.loc_escape('a "b"\\c\nd'), 'a \\"b\\"\\\\c\\nd')

    def test_is_picture_path(self) -> None:
        """Paths are told apart from sprite names."""
        for value in ("pictures/a.png", "pictures\\a.png", "war.png", "x.JPG"):
            self.assertTrue(modgen.is_picture_path(value), value)
        for value in ("GFX_report_event_generic", "GFX_a"):
            self.assertFalse(modgen.is_picture_path(value), value)

    def test_image_size(self) -> None:
        """Sizes are read from PNG and DDS headers."""
        with tempfile.TemporaryDirectory() as tmp:
            png = Path(tmp) / "a.png"
            dds = Path(tmp) / "a.dds"
            make_png(png, 210, 176)
            make_dds(dds, 397, 153)
            self.assertEqual(
                modgen.image_size(png.read_bytes()[:24], ".png"), (210, 176)
            )
            self.assertEqual(
                modgen.image_size(dds.read_bytes()[:24], ".dds"), (397, 153)
            )
        self.assertIsNone(modgen.image_size(b"short", ".png"))


if __name__ == "__main__":
    unittest.main()
