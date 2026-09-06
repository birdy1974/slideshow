"""Project files: "Save project" / "Load project" as a file on a mounted volume.

SQLite stays the working store; a project file is a portable copy of the same
snapshot. These tests pin the two rules that keep the feature inside the
container's security model — only the writable `/output` mount accepts a save,
and a filename can never leave the folder it was aimed at — plus the round trip,
the overwrite handshake, the honest errors, and the constants the browser half
(`src/projectFiles.ts`) has to agree on.
"""
from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path
from typing import Any

from app.config import Settings
from app.media import UnsafePath, browse
from app.project_files import (
    MAX_NAME_LENGTH,
    MAX_PROJECT_BYTES,
    PROJECT_SUFFIX,
    WRITABLE_ROOTS,
    ProjectFileExistsError,
    ReadOnlyMountError,
    project_file_info,
    project_filename,
    read_project_file,
    safe_stem,
    ui_path,
    write_project_file,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_MODULE = REPO_ROOT / "src" / "projectFiles.ts"


def snapshot(**overrides: Any) -> dict[str, Any]:
    project = {
        "schemaVersion": 1,
        "project": {"name": "Portugal summer", "randomOrder": False},
        "media": [{"id": 1, "type": "image", "path": "/photos/trip/a.jpg", "duration": 4}],
        "output": {"path": "/output", "filename": "Portugal summer"},
    }
    project.update(overrides)
    return project


class SharedConstantsTest(unittest.TestCase):
    """The browser and the backend must name files the same way."""

    def test_typescript_agrees_on_the_constants(self) -> None:
        source = FRONTEND_MODULE.read_text(encoding="utf-8")
        suffix = re.search(r"export const PROJECT_SUFFIX = '([^']+)'", source)
        self.assertIsNotNone(suffix, "PROJECT_SUFFIX is not exported by src/projectFiles.ts")
        self.assertEqual(suffix.group(1), PROJECT_SUFFIX)
        roots = re.search(r"export const WRITABLE_ROOTS = \[([^\]]*)\]", source)
        self.assertIsNotNone(roots, "WRITABLE_ROOTS is not exported by src/projectFiles.ts")
        self.assertEqual([part.strip().strip("'\"") for part in roots.group(1).split(",") if part.strip()],
                         list(WRITABLE_ROOTS))
        length = re.search(r"export const MAX_NAME_LENGTH = (\d+)", source)
        self.assertIsNotNone(length)
        self.assertEqual(int(length.group(1)), MAX_NAME_LENGTH)

    def test_typescript_rejects_the_same_characters(self) -> None:
        # Both sides replace the same illegal set with a dash; a divergence would
        # let the picker promise a filename the backend then refuses.
        source = FRONTEND_MODULE.read_text(encoding="utf-8")
        self.assertIn(r'/[\\/:*?"<>|\u0000-\u001f]/g', source)
        self.assertIn(r"^[\s.-]+|[\s.-]+$", source, "the edge trimming must match too")
        self.assertIn("export function projectFileStem", source)


class FilenameTest(unittest.TestCase):
    def test_a_plain_name_gains_the_suffix(self) -> None:
        self.assertEqual(project_filename("Portugal summer"), "Portugal summer.slideshow.json")

    def test_an_existing_suffix_is_not_doubled(self) -> None:
        for name, want in (("trip.slideshow.json", "trip.slideshow.json"), ("trip.json", "trip.slideshow.json"),
                           ("TRIP.JSON", "TRIP.slideshow.json"), ("trip.SLIDESHOW.JSON", "trip.slideshow.json")):
            self.assertEqual(project_filename(name), want, name)

    def test_a_path_keeps_only_its_last_component(self) -> None:
        for name in ("a/b/c", "..\\..\\windows\\system32\\x", "/etc/passwd", "trip/holiday.json"):
            result = project_filename(name)
            self.assertNotIn("/", result)
            self.assertNotIn("\\", result)
            self.assertTrue(result.endswith(PROJECT_SUFFIX))
        self.assertEqual(project_filename("/etc/passwd"), "passwd.slideshow.json")
        self.assertEqual(project_filename("trip/holiday.json"), "holiday.slideshow.json")

    def test_illegal_characters_become_a_dash(self) -> None:
        self.assertEqual(project_filename('My: "trip" <2026>'), "My- -trip- -2026.slideshow.json")
        stem = safe_stem('a:b*c?d|e<f>g"h')
        self.assertNotRegex(stem, r'[:*?<>|"\\/]')

    def test_edges_are_trimmed_and_length_capped(self) -> None:
        self.assertEqual(safe_stem("  ...trip...  "), "trip")
        self.assertEqual(safe_stem("- -"), "")
        self.assertLessEqual(len(safe_stem("x" * 500)), MAX_NAME_LENGTH)

    def test_nothing_usable_left_falls_back(self) -> None:
        for name in ("", "   ", "..", None, 42, [], {}):
            self.assertEqual(project_filename(name), "project.slideshow.json", repr(name))

    def test_ui_path_is_what_the_picker_shows(self) -> None:
        self.assertEqual(ui_path("output", "trip/holiday.slideshow.json"), "/output/trip/holiday.slideshow.json")
        self.assertEqual(ui_path("output", ""), "/output")
        self.assertEqual(ui_path("photos", "/a/b"), "/photos/a/b")


class BrowseProjectFilesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        for name in ("config", "photos", "videos", "music", "output"):
            (base / name).mkdir()
        self.settings = Settings(base / "config", base / "photos", base / "videos", base / "music", base / "output")
        out = self.settings.output_dir
        (out / "trip").mkdir()
        (out / "trip" / "holiday.slideshow.json").write_text(json.dumps(snapshot()))
        (out / "notes.json").write_text("{}")
        (out / "movie.mp4").write_bytes(b"x" * 8)
        (out / ".hidden.json").write_text("{}")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_project_mode_lists_folders_and_json_files_only(self) -> None:
        entries = browse(self.settings, "output", "", project_files=True)["entries"]
        by_name = {entry["name"]: entry for entry in entries}
        self.assertEqual(set(by_name), {"trip", "notes.json"})
        self.assertEqual(by_name["trip"]["kind"], "directory")
        self.assertEqual(by_name["notes.json"]["kind"], "project")
        self.assertEqual(by_name["notes.json"]["relativePath"], "notes.json")

    def test_subfolders_are_walked(self) -> None:
        entries = browse(self.settings, "output", "trip", project_files=True)["entries"]
        self.assertEqual([entry["name"] for entry in entries], ["holiday.slideshow.json"])
        self.assertEqual(entries[0]["kind"], "project")
        self.assertGreater(entries[0]["size"], 0)

    def test_read_only_mounts_can_be_browsed_for_projects(self) -> None:
        (self.settings.photos_dir / "trip.json").write_text("{}")
        entries = browse(self.settings, "photos", "", project_files=True)["entries"]
        self.assertEqual([entry["name"] for entry in entries], ["trip.json"])

    def test_media_browsing_is_unchanged(self) -> None:
        entries = browse(self.settings, "output", "", folders_only=True)["entries"]
        self.assertEqual([entry["name"] for entry in entries], ["trip"], "the destination picker still shows folders only")
        with self.assertRaises(UnsafePath):
            browse(self.settings, "output", "")
        names = [entry["name"] for entry in browse(self.settings, "photos", "", project_files=True)["entries"]]
        self.assertNotIn("movie.mp4", names)


class WriteReadTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        for name in ("config", "photos", "videos", "music", "output"):
            (base / name).mkdir()
        self.settings = Settings(base / "config", base / "photos", base / "videos", base / "music", base / "output")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def save(self, **kwargs) -> dict[str, Any]:
        options = {"root": "output", "folder": "", "filename": "Portugal summer", "snapshot": snapshot(), "overwrite": False}
        options.update(kwargs)
        return write_project_file(self.settings, options.pop("root"), options.pop("folder"),
                                  options.pop("filename"), options.pop("snapshot"), options.pop("overwrite"))

    def test_round_trip(self) -> None:
        saved = self.save()
        self.assertEqual(saved["name"], "Portugal summer.slideshow.json")
        self.assertEqual(saved["path"], "/output/Portugal summer.slideshow.json")
        self.assertEqual(saved["items"], 1)
        self.assertFalse(saved["overwritten"])
        target = self.settings.output_dir / "Portugal summer.slideshow.json"
        self.assertTrue(target.is_file())
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), snapshot())
        self.assertEqual(read_project_file(self.settings, "output", "Portugal summer.slideshow.json"), snapshot())

    def test_the_file_is_pretty_json_a_human_can_read(self) -> None:
        self.save()
        text = (self.settings.output_dir / "Portugal summer.slideshow.json").read_text(encoding="utf-8")
        self.assertIn("\n  ", text)
        self.assertIn("Portugal summer", text)

    def test_missing_folders_are_created(self) -> None:
        saved = self.save(folder="projects/2026")
        self.assertEqual(saved["path"], "/output/projects/2026/Portugal summer.slideshow.json")
        self.assertEqual(saved["folder"], "/output/projects/2026")
        self.assertTrue((self.settings.output_dir / "projects" / "2026" / saved["name"]).is_file())

    def test_no_temporary_files_are_left_behind(self) -> None:
        self.save()
        leftovers = [path.name for path in self.settings.output_dir.iterdir() if path.name.endswith(".part") or path.name.startswith(".")]
        self.assertEqual(leftovers, [])

    def test_saving_again_needs_overwrite(self) -> None:
        self.save()
        with self.assertRaises(ProjectFileExistsError) as caught:
            self.save()
        self.assertEqual(caught.exception.ui_path, "/output/Portugal summer.slideshow.json")
        replaced = self.save(overwrite=True, snapshot=snapshot(project={"name": "Portugal winter"}))
        self.assertTrue(replaced["overwritten"])
        info = project_file_info(self.settings, "output", replaced["name"])
        self.assertEqual(info["projectName"], "Portugal winter")

    def test_a_changed_project_replaces_the_bytes(self) -> None:
        self.save()
        self.save(overwrite=True, snapshot=snapshot(media=[{"id": 1}, {"id": 2}, {"id": 3}]))
        info = project_file_info(self.settings, "output", "Portugal summer.slideshow.json")
        self.assertEqual(info["items"], 3)

    def test_only_the_writable_mount_accepts_a_save(self) -> None:
        for root in ("photos", "videos", "music"):
            with self.subTest(root=root), self.assertRaises(ReadOnlyMountError):
                self.save(root=root)
            self.assertEqual(list(self.settings.media_roots[root].iterdir()), [], f"{root} must stay untouched")

    def test_a_filename_cannot_leave_its_folder(self) -> None:
        for name in ("../../evil", "/etc/passwd", "..\\..\\evil", "trip/../../escape"):
            saved = self.save(filename=name, overwrite=True)
            written = Path(self.settings.output_dir / saved["name"])
            self.assertTrue(written.is_file(), saved["name"])
            self.assertEqual(written.parent, self.settings.output_dir)
        # Nothing landed outside the mount it was aimed at.
        base = Path(self.temp.name)
        self.assertEqual(sorted(child.name for child in base.iterdir()),
                         ["config", "music", "output", "photos", "videos"])
        self.assertEqual(sorted(child.name for child in base.parent.iterdir() if child.name.startswith("evil") or child.name == "escape"), [])

    def test_a_folder_cannot_escape_the_mount(self) -> None:
        for folder in ("../config", "../../etc", "trip/../../config"):
            with self.subTest(folder=folder), self.assertRaises(UnsafePath):
                self.save(folder=folder)

    def test_a_leading_slash_is_relative_to_the_mount(self) -> None:
        # safe_path() strips it, so "/etc" means /output/etc — inside the mount.
        saved = self.save(folder="/etc")
        self.assertEqual(saved["path"], "/output/etc/Portugal summer.slideshow.json")
        self.assertTrue((self.settings.output_dir / "etc" / saved["name"]).is_file())
        self.assertFalse(Path("/etc/Portugal summer.slideshow.json").exists())

    def test_an_unknown_volume_is_refused(self) -> None:
        with self.assertRaises(UnsafePath):
            self.save(root="backup")

    def test_read_errors_are_specific(self) -> None:
        out = self.settings.output_dir
        cases = {
            "missing.slideshow.json": FileNotFoundError,
            "movie.mp4": ValueError,
            "empty.json": ValueError,
            "broken.json": ValueError,
            "list.json": ValueError,
            "not-a-project.json": ValueError,
        }
        (out / "movie.mp4").write_bytes(b"x")
        (out / "empty.json").write_bytes(b"")
        (out / "broken.json").write_text("{not json")
        (out / "list.json").write_text("[1, 2, 3]")
        (out / "not-a-project.json").write_text('{"hello": "world"}')
        for name, error in cases.items():
            with self.subTest(name=name), self.assertRaises(error):
                read_project_file(self.settings, "output", name)

    def test_a_huge_file_is_not_a_project(self) -> None:
        out = self.settings.output_dir
        (out / "big.json").write_text(json.dumps(snapshot(media=[{"id": index} for index in range(4000)])))
        from app import project_files
        original = project_files.MAX_PROJECT_BYTES
        project_files.MAX_PROJECT_BYTES = 512
        try:
            with self.assertRaises(ValueError):
                read_project_file(self.settings, "output", "big.json")
        finally:
            project_files.MAX_PROJECT_BYTES = original
        self.assertGreater(MAX_PROJECT_BYTES, 1024 * 1024)

    def test_info_reports_what_the_picker_shows(self) -> None:
        self.save()
        info = project_file_info(self.settings, "output", "Portugal summer.slideshow.json")
        self.assertEqual(info["root"], "output")
        self.assertEqual(info["name"], "Portugal summer.slideshow.json")
        self.assertEqual(info["projectName"], "Portugal summer")
        self.assertEqual(info["items"], 1)
        self.assertGreater(info["size"], 0)
        self.assertGreater(info["modified"], 0)
        self.assertEqual(info["project"], snapshot())

    def test_reading_a_folder_is_an_error_not_a_crash(self) -> None:
        (self.settings.output_dir / "trip").mkdir()
        with self.assertRaises((ValueError, FileNotFoundError, IsADirectoryError, OSError)):
            read_project_file(self.settings, "output", "trip")

    def test_traversal_in_the_read_path_is_refused(self) -> None:
        with self.assertRaises(UnsafePath):
            read_project_file(self.settings, "output", "../config/slideshow.db")


class ProjectFileEndpointTest(unittest.TestCase):
    """GET/POST /api/project-files — what the browse popup actually calls."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        for name in ("config", "photos", "videos", "music", "output"):
            (base / name).mkdir()
        self.settings = Settings(base / "config", base / "photos", base / "videos", base / "music", base / "output")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def post(self, **kwargs) -> Any:
        from unittest import mock

        from app.main import ProjectFileSave, save_project_file
        body = {"root": "output", "folder": "", "filename": "Portugal summer", "overwrite": False,
                "project": snapshot()}
        body.update(kwargs)
        with mock.patch("app.main.settings", self.settings):
            return save_project_file(ProjectFileSave(**body))

    def get(self, root: str = "output", path: str = "Portugal summer.slideshow.json") -> Any:
        from unittest import mock

        from app.main import read_project_file as endpoint
        with mock.patch("app.main.settings", self.settings):
            return endpoint(root, path)

    def test_save_then_read(self) -> None:
        saved = self.post()
        self.assertEqual(saved["path"], "/output/Portugal summer.slideshow.json")
        info = self.get()
        # ProjectPayload fills in the optional sections it knows about, so the
        # file also carries empty soundtrack/textDefaults/timeline objects.
        self.assertEqual(info["project"]["project"], snapshot()["project"])
        self.assertEqual(info["project"]["media"], snapshot()["media"])
        self.assertEqual(info["project"]["output"], snapshot()["output"])
        self.assertEqual(info["projectName"], "Portugal summer")

    def test_status_codes(self) -> None:
        from fastapi import HTTPException

        def status(call) -> int:
            with self.assertRaises(HTTPException) as caught:
                call()
            return caught.exception.status_code

        self.post()
        self.assertEqual(status(lambda: self.post()), 409, "an existing file needs an explicit overwrite")
        self.assertEqual(status(lambda: self.post(root="photos")), 403, "the media mounts are read-only")
        self.assertEqual(status(lambda: self.post(folder="../config")), 400, "traversal is refused")
        self.assertEqual(status(lambda: self.get(path="missing.slideshow.json")), 404)
        (self.settings.output_dir / "movie.mp4").write_bytes(b"x")
        self.assertEqual(status(lambda: self.get(path="movie.mp4")), 422, "not a project file")
        self.assertEqual(status(lambda: self.get(root="backup")), 400)

    def test_the_conflict_carries_the_path_like_the_render_does(self) -> None:
        from fastapi import HTTPException

        self.post()
        with self.assertRaises(HTTPException) as caught:
            self.post()
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.detail["code"], "project_exists")
        self.assertEqual(caught.exception.detail["path"], "/output/Portugal summer.slideshow.json")

    def test_overwrite_is_accepted(self) -> None:
        self.post()
        saved = self.post(overwrite=True, project=snapshot(project={"name": "Portugal winter"}))
        self.assertTrue(saved["overwritten"])
        self.assertEqual(self.get()["projectName"], "Portugal winter")

    def test_a_media_reference_outside_the_mounts_is_refused(self) -> None:
        from fastapi import HTTPException

        bad = snapshot(media=[{"id": 1, "type": "image", "path": "/elsewhere/a.jpg"}])
        with self.assertRaises(HTTPException) as caught:
            self.post(project=bad)
        self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(list(self.settings.output_dir.iterdir()), [], "nothing is written when the payload is refused")


if __name__ == "__main__":
    unittest.main()
