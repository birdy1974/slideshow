"""Which pictures the app may offer as a background, from the storyline itself.

"Select a photo from the overall timeline" is a list built out of the slides, so the
rules about what may appear on it — a movie may not, a collage contributes each of
its photos, a legacy item stores its folder in `path` and its file in `name` — are
rules about files the render has to be able to open. Getting one wrong is a background
that silently is not there, or a picker full of rows `source_path` cannot resolve.

`src/timelinePhotos.ts` is pure and imports nothing, which is what lets this run it
under node (`timeline_photos.mjs`) against the module the popup actually uses rather
than against a copy of it in Python.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DRIVER = Path(__file__).with_name("timeline_photos.mjs")


class TimelinePicturesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.node = shutil.which("node")
        if not cls.node or not DRIVER.exists():
            raise unittest.SkipTest("needs node and the timeline pictures driver")

    def _run(self, cases: list[dict]) -> list[dict]:
        proc = subprocess.run(
            [self.node, "--experimental-strip-types", str(DRIVER)],
            input=json.dumps({"cases": cases}), capture_output=True, text=True, timeout=180,
        )
        if proc.returncode != 0:
            self.fail(f"timeline photos driver failed: {proc.stderr.strip()[:2000]}")
        return json.loads(proc.stdout)

    def test_the_timeline_contributes_pictures_in_slide_order(self) -> None:
        media = [
            {"id": 1, "name": "a.jpg", "path": "/photos/x/a.jpg", "type": "image"},
            {"id": 2, "name": "holiday.mp4", "path": "/videos/holiday.mp4", "type": "video"},
            {"id": 3, "name": "Collage", "path": "", "type": "title", "collage": {"photos": [
                {"path": "y/b.jpg", "name": "b.jpg"},
                {"path": "/photos/y/c.jpg"},
            ]}},
            {"id": 4, "name": "Title", "path": "", "type": "title"},
            {"id": 5, "name": "d.jpg", "path": "/uploads/u/d.jpg", "type": "image"},
        ]
        row = self._run([{"media": media}])[0]
        got = row["pictures"]
        self.assertEqual([p["slide"] for p in got], [1, 3, 3, 5],
                         "a movie and a bare title frame contribute nothing; a collage contributes all of its photos")
        self.assertEqual([p["photo"] for p in got], [0, 1, 2, 0],
                         "the number inside the slide, 0 when the slide is itself one picture")
        self.assertEqual([p["name"] for p in got], ["a.jpg", "b.jpg", "c.jpg", "d.jpg"])
        self.assertEqual([p["path"] for p in got],
                         ["/photos/x/a.jpg", "/photos/y/b.jpg", "/photos/y/c.jpg", "/uploads/u/d.jpg"],
                         "whole and mount-first, which is the only shape the backend resolves")
        self.assertEqual(row["roots"],
                         ["photos|/photos/x/a.jpg", "photos|/photos/y/b.jpg",
                          "photos|/photos/y/c.jpg", "uploads|/uploads/u/d.jpg"],
                         "the mount comes along, or the thumbnail cannot be requested at all")
        self.assertEqual(row["labels"], ["Slide 1", "Slide 3 · photo 1 of 2", "Slide 3 · photo 2 of 2", "Slide 5"],
                         "every row says where its picture came from")

    def test_a_legacy_path_of_folder_plus_name_still_resolves(self) -> None:
        row = self._run([
            {"media": [{"id": 1, "name": "a.jpg", "path": "/photos/x", "type": "image"}]},
            {"media": [{"id": 1, "name": "m.mp4", "path": "/videos", "type": "image"}]},
            {"media": []},
        ])
        self.assertEqual(row[0]["pictures"][0]["path"], "/photos/x/a.jpg",
                         "older snapshots keep the folder in `path`; the entry is still a whole path")
        self.assertEqual(row[1]["pictures"][0]["path"], "/videos/m.mp4")
        self.assertEqual(row[1]["pictures"][0]["rootName"], "videos")
        self.assertEqual(row[2]["pictures"], [], "an empty storyline offers nothing, which is not an error")

    def test_reuse_is_counted_before_any_deduping(self) -> None:
        """The same file on two slides is one row in a picker and still *two* uses.

        A background that is also one of the photos in front of it is usually a
        mistake, so the strip has to be able to say "used twice" — which means the
        count is taken from the timeline, not from the list the browser shows.
        """
        media = [
            {"id": 1, "name": "a.jpg", "path": "/photos/x/a.jpg", "type": "image"},
            {"id": 2, "name": "b.jpg", "path": "/photos/x/b.jpg", "type": "image"},
            {"id": 3, "name": "Again", "path": "", "type": "title", "collage": {"photos": [{"path": "/photos/x/a.jpg"}]}},
        ]
        row = self._run([{"media": media}])[0]
        self.assertEqual(row["reuse"],
                         {"photos|/photos/x/a.jpg": 2, "photos|/photos/x/b.jpg": 1},
                         "keyed by the whole path, so the same file on two mounts is two entries")
        self.assertEqual(len(row["pictures"]), 3, "the module lists the timeline; the picker decides to collapse it")

    def test_a_missing_file_never_becomes_a_row(self) -> None:
        row = self._run([{"media": [
            {"id": 1, "name": "", "path": "", "type": "image"},
            {"id": 2, "name": "c.jpg", "path": "/photos/y/c.jpg", "type": "image"},
            {"id": 3, "name": "Collage", "path": "", "type": "title", "collage": {"photos": [{"path": ""}, None]}},
        ]}])[0]
        self.assertEqual([p["name"] for p in row["pictures"]], ["c.jpg"],
                         "an item with no file is nothing the render could open")

    def test_the_path_split_is_the_same_rule_the_movie_strip_uses(self) -> None:
        row = self._run([
            {"media": [], "split": {"path": "/uploads/holiday/day1/d.jpg", "name": ""}},
            {"media": [], "split": {"path": "/videos/clip.mp4", "name": "clip.mp4"}},
            {"media": [], "split": {"path": "loose.jpg", "name": ""}},
            {"media": [], "split": {"path": "/uploads/holiday", "name": "d.jpg"}},
        ])
        self.assertEqual([row[i]["split"] for i in range(4)], [
            {"root": "uploads", "relative": "holiday/day1/d.jpg"},
            {"root": "videos", "relative": "clip.mp4"},
            {"root": "photos", "relative": "loose.jpg"},
            {"root": "uploads", "relative": "holiday/d.jpg"},
        ], "every mount this app writes, and both shapes `path` has ever had: whole file, or folder plus name")
    def test_no_pictures_no_rows(self) -> None:
        for media in (None, {}, [], [{"type": "video", "path": "/videos/a.mp4", "name": "a.mp4"}]):
            row = self._run([{"media": media if isinstance(media, list) or media is None else []}])[0]
            self.assertEqual(row["pictures"], [], f"{media!r} must not invent a row")


class CountLabelTest(unittest.TestCase):
    """The "N pictures" line in the timeline view is built, not pasted.

    A nested template whose inner quote closes early prints ``4 picture${4}``
    where it should print ``4 pictures`` — visible on screen, invisible to a
    grep for the word, so the label is checked for its literal braces.
    """

    APP = (REPO / "src" / "App.tsx").read_text(encoding="utf-8")

    def label(self) -> str:
        start = self.APP.index('className="file-count">')
        return self.APP[start:self.APP.index("</span>", start)]

    def test_the_label_counts_pictures_in_timeline_view_and_files_elsewhere(self):
        label = self.label()
        self.assertIn("picture${shown.length===1?'':'s'}", label)
        self.assertIn("file${shown.length===1?'':'s'}", label)
        self.assertNotIn("'picture${'", label, "a brace inside a quoted string is printed, not interpolated")

    def test_the_count_comes_from_the_list_actually_drawn(self):
        # `shown` is the timeline list when the timeline view is on, `entries` is not
        label = self.label()
        self.assertIn("shown.length?", label)
        self.assertNotIn("entries.length?", label)



class StoredShapeTest(unittest.TestCase):
    """What a pick writes has to be what a project already stores, or the save fails.

    A background is only ever a path string, and the save endpoint resolves it with
    `source_path`, which reads the mount from the first segment. A picker that hands
    over `Holiday/x.jpg` instead of `/photos/Holiday/x.jpg` therefore does not look a
    picture up — it breaks the slide: the project can be saved, rendered, nor opened
    without a 422. These tests take the entry the popup writes straight into the
    resolver, so the two halves are measured against each other instead of against
    each author's idea of what a path looks like.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.node = shutil.which("node")
        if not cls.node or not DRIVER.exists():
            raise unittest.SkipTest("needs node and the timeline pictures driver")

    def _run(self, cases: list[dict]) -> list[dict]:
        proc = subprocess.run(
            [self.node, "--experimental-strip-types", str(DRIVER)],
            input=json.dumps({"cases": cases}), capture_output=True, text=True, timeout=180,
        )
        if proc.returncode != 0:
            self.fail(f"timeline photos driver failed: {proc.stderr.strip()[:2000]}")
        return json.loads(proc.stdout)

    def test_an_entry_is_a_path_the_browser_itself_would_have_listed(self) -> None:
        # `/api/media/list` builds an entry's path as f"/{root}/{relative}" — see the
        # line asserted below. The timeline list is built out of stored paths instead,
        # so it has to put the mount back on exactly the same way: one shape, both
        # doors. If the listing ever changes shape, this fails and says so.
        listing = (REPO / "backend" / "app" / "media.py").read_text(encoding="utf-8")
        self.assertIn('"path": f"/{root_name}/{rel}"', listing, "the shape the listing emits")
        got = self._run([{"media": [
            {"id": 1, "name": "x.jpg", "path": "/photos/Holiday/x.jpg", "type": "image"},
            {"id": 2, "name": "Collage", "path": "", "type": "title",
             "collage": {"photos": [{"path": "Holiday/y.jpg", "name": "y.jpg"}]}},
        ]}])[0]["pictures"]
        self.assertEqual([p["path"] for p in got], ["/photos/Holiday/x.jpg", "/photos/Holiday/y.jpg"])
        for picture in got:
            self.assertRegex(picture["path"], r"^/(photos|videos|music|uploads)/",
                             "a stored path that names no mount is a rejected project")

    def test_a_picked_background_resolves_to_the_file_on_disk(self) -> None:
        from app.config import Settings
        from app.media import source_path

        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            for name in ("config", "photos", "videos", "music", "output"):
                (base / name).mkdir()
            settings = Settings(base / "config", base / "photos", base / "videos", base / "music", base / "output")
            folder = settings.photos_dir / "Holiday 2026"
            folder.mkdir(parents=True)
            target = folder / "x.jpg"
            target.write_bytes(b"jpg")

            entry = self._run([{"media": [
                {"id": 1, "name": "x.jpg", "path": "/photos/Holiday 2026/x.jpg", "type": "image"},
            ]}])[0]["pictures"][0]
            self.assertEqual(source_path(settings, {"path": entry["path"], "name": ""}), target,
                             "the value the strip stores is the value the save resolves")
            # …and the shorter shape a project saved from the released picker holds is
            # no longer fatal: it is looked for under the mounts until it is found.
            self.assertEqual(source_path(settings, {"path": "Holiday 2026/x.jpg", "name": ""}), target,
                             "a prefix-less background still finds its file")


class BackgroundSurfaceTest(unittest.TestCase):
    """The two places the option appears, and the value each writes.

    Both write the same field the media browser writes, because "the render can open
    it" is the only requirement a background has — a strip that invented its own path
    shape would look fine in the popup and fail in the MP4.
    """

    def setUp(self) -> None:
        self.app = (REPO / "src" / "App.tsx").read_text(encoding="utf-8")

    def test_the_background_pickers_offer_the_timeline(self) -> None:
        self.assertEqual(self.app.count("<TimelinePhotoStrip"), 2,
                         "the collage popup and the text-frame popup, one each")
        self.assertIn("backgroundImage: p.path", self.app,
                      "the strip writes the same field, in the same shape, as the browser does")
        self.assertIn("frameBackgroundImage: p.path", self.app)

    def test_the_strip_stores_what_the_browser_stores(self) -> None:
        """The strip is a shortcut to the same value, never a second scheme."""
        strip = (REPO / "src" / "TimelinePhotoStrip.tsx").read_text(encoding="utf-8")
        self.assertIn("onPick={p => setSpec({ backgroundImage: p.path })}", self.app)
        self.assertIn("onPick={p => apply({ frameBackgroundImage: p.path } as any)}", self.app)
        self.assertIn("current === picture.path || afterMount(current) === afterMount(picture.path)", strip,
                      "a background stored the short way is still shown as the one in use")
        self.assertIn(r"replace(/^\/(photos|videos|music|uploads)\//", strip,
                      "the mount is the only difference between the two shapes, so it is what is ignored")
        core = (REPO / "src" / "timelinePhotos.ts").read_text(encoding="utf-8")
        self.assertIn("return `/${root}/${String(relative", core,
                      "the list puts the mount back on rather than passing the short part through")

    def test_the_browser_grows_a_timeline_location(self) -> None:
        self.assertIn("timeline?: TimelinePicture[]", self.app, "the picker takes the list, it does not rebuild it")
        self.assertIn("setTimelineView(true)", self.app)
        self.assertIn("const shown=timelineView?timelineEntries:entries", self.app,
                      "the view swaps the list the existing cards draw, rather than forking the grid")
        self.assertIn("timeline={timelinePictures(media)}", self.app)
        self.assertGreaterEqual(self.app.count("timeline={timelinePictures(media)}"), 6,
                                "every picture picker: collage, replace, both backgrounds, and both popups")


if __name__ == "__main__":
    unittest.main()
