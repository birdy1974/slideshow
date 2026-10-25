"""The media browser must stay usable while a folder's pictures are arriving.

A NAS folder can hold hundreds of photos. When every card asked for its
full-size original at once, the browser's few connections per host filled up and
decoding tied up the main thread, so clicking a file appeared to do nothing
until the whole folder had loaded. The popup therefore queues thumbnails
through `src/mediaThumbs.ts` (viewport first, then a shared slot) and offers a
names-only list view that requests no pictures at all — while the default view
stays the thumbnail grid.

Python models the queue independently and the Node driver (tests/media_thumbs.mjs)
replays the same scripts on the TypeScript the browser runs; the two must agree
step for step. The rest of the file pins the wiring in ``src/App.tsx`` that the
behaviour depends on: selection never waits for a thumbnail, and the list branch
mounts no ``<img>``/``<video>``.
"""
from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path

DRIVER = Path(__file__).resolve().parent / "media_thumbs.mjs"
REPO_ROOT = Path(__file__).resolve().parents[2]
THUMBS_MODULE = REPO_ROOT / "src" / "mediaThumbs.ts"
APP_SOURCE = REPO_ROOT / "src" / "App.tsx"
STYLES = REPO_ROOT / "src" / "styles.css"


def _run_node(cases: list[dict]) -> dict:
    proc = subprocess.run(
        ["node", "--experimental-strip-types", str(DRIVER)],
        input=json.dumps({"cases": cases}), capture_output=True, text=True,
        timeout=120, cwd=Path(__file__).resolve().parent,
    )
    if proc.returncode != 0:
        raise AssertionError(f"thumbnail queue driver failed:\n{proc.stderr}\n{proc.stdout}")
    return {row["id"]: row for row in json.loads(proc.stdout)}


class Queue:
    """A second implementation of the same rules: at most ``slots`` cards may
    download, the bench is served first-in-first-out, and a card that walks away
    before its turn just leaves the bench."""

    def __init__(self, slots: int) -> None:
        self.slots = slots
        self.running = 0
        self.waiters: list[str] = []
        self.state: dict[str, str] = {}
        self.ready: list[str] = []

    def reserve(self, card: str) -> None:
        assert card not in self.state, "each card reserves once per scenario"
        if self.running < self.slots:
            self.running += 1
            self.state[card] = "running"
            self.ready.append(card)
        else:
            self.state[card] = "queued"
            self.waiters.append(card)

    def release(self, card: str, twice: bool = False) -> None:
        for _ in range(2 if twice else 1):
            if self.state.get(card) == "queued":
                self.waiters.remove(card)
                self.state[card] = "released"
            elif self.state.get(card) == "running":
                self.running -= 1
                self.state[card] = "released"
                if self.waiters:
                    nxt = self.waiters.pop(0)
                    self.running += 1
                    self.state[nxt] = "running"
                    self.ready.append(nxt)

    def snapshot(self) -> dict:
        return {"inFlight": self.running, "queued": len(self.waiters), "ready": list(self.ready)}

    def drain(self) -> None:
        for card in list(self.state):
            self.release(card)


class ThumbnailQueueTest(unittest.TestCase):
    """The shared download queue that keeps the picker responsive."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = [
            # A folder full of pictures: only `slots` of them may stream, the
            # rest wait — and the waiting cards are clickable meanwhile.
            {"id": "flood", "kind": "script", "steps": [
                {"reserve": [str(i) for i in range(10)]},
            ]},
            # Draining hands the slots to the bench in the order cards appeared,
            # so the grid fills top-down instead of in a random burst.
            {"id": "drain", "kind": "script", "steps": [
                {"reserve": [str(i) for i in range(7)]},
                *[{"release": [str(i)]} for i in range(7)],
            ]},
            # A card scrolled past before its turn leaves the queue without
            # costing a slot, and the next card in line still gets woken up.
            {"id": "cancel_queued", "kind": "script", "steps": [
                {"reserve": ["a", "b", "c", "d", "e", "f"]},
                {"release": ["e"]},
                {"release": ["a"]},
                {"release": ["g"], "again": True},
            ]},
            # The release is wired to both the load event and the unmount
            # cleanup, so calling it twice must not free a second slot.
            {"id": "double_release", "kind": "script", "steps": [
                {"reserve": ["a", "b", "c", "d", "e"]},
                {"release": ["b"], "again": True},
                {"reserve": ["f", "g"]},
                {"release": ["a", "c", "d", "e", "f", "g"], "again": True},
            ]},
            # Only pictures and movies are worth a request; folders, music and
            # anything else stay an icon and never touch the network.
            {"id": "kinds", "kind": "classification", "files": [
                {"kind": "image"}, {"kind": "video"}, {"kind": "directory"},
                {"kind": "audio"}, {"kind": "project"}, {"kind": None}, {},
                {"kind": "IMAGE"}, {"kind": 1},
                # A 0-byte file only answers with an error, so it must not even
                # take a place in the queue.
                {"kind": "image", "empty": True}, {"kind": "video", "empty": True},
                {"kind": "image", "empty": False},
            ]},
            {"id": "viewport", "kind": "viewport"},
        ]
        cls.results = _run_node(cls.cases)

    def _model(self, case: dict) -> list[dict]:
        slots = self.results["constants"]["slots"]
        queue = Queue(slots)
        steps = []
        for step in case["steps"]:
            for card in step.get("reserve", []):
                queue.reserve(card)
            for card in step.get("release", []):
                queue.release(card, twice=bool(step.get("again")))
            steps.append(queue.snapshot())
        queue.drain()
        return steps

    def test_flood_is_capped_at_the_shared_slots(self) -> None:
        case = next(c for c in self.cases if c["id"] == "flood")
        slots = self.results["constants"]["slots"]
        self.assertEqual(self.results["flood"]["steps"], self._model(case))
        self.assertGreaterEqual(slots, 2, "a queue of one makes a slow folder feel dead")
        self.assertLessEqual(slots, 5, "the popup must leave browser connections for the API")
        state = self.results["flood"]["steps"][0]
        self.assertEqual(state["inFlight"], slots)
        self.assertEqual(state["queued"], 10 - slots)
        self.assertEqual(state["ready"], [str(i) for i in range(slots)])

    def test_scripts_match_the_independent_model(self) -> None:
        for case in self.cases:
            if case["kind"] != "script":
                continue
            with self.subTest(case=case["id"]):
                self.assertEqual(self.results[case["id"]]["steps"], self._model(case))
                slots = self.results["constants"]["slots"]
                for state in self.results[case["id"]]["steps"]:
                    self.assertLessEqual(state["inFlight"], slots)
                    self.assertLessEqual(state["inFlight"] + state["queued"], 10)

    def test_no_card_holds_a_slot_after_its_folder_changed(self) -> None:
        # Every released reservation has to give its slot back, otherwise the
        # queue wedges after a few folder changes and no thumbnail ever loads.
        for case in self.cases:
            if case["kind"] != "script":
                continue
            with self.subTest(case=case["id"]):
                self.assertEqual(self.results[case["id"]]["leftoverInFlight"], 0)
                self.assertEqual(self.results[case["id"]]["leftoverQueued"], 0)

    def test_only_pictures_are_requested(self) -> None:
        self.assertEqual(self.results["kinds"]["thumbnail"], [True, True, False, False, False, False, False, False, False,
                                    False, False, True])

    def test_watcher_without_intersection_observer_starts_immediately(self) -> None:
        row = self.results["viewport"]
        self.assertEqual(row["calls"], 1, "a card must be woken exactly once")
        self.assertTrue(row["stopIsFunction"])

    def test_margin_is_a_scroll_hint_not_a_stall(self) -> None:
        margin = self.results["constants"]["margin"]
        self.assertGreaterEqual(margin, 120, "a short margin flashes placeholders while scrolling")
        self.assertLessEqual(margin, 600, "loading everything defeats the queue")

    def test_a_stalled_picture_gives_its_slot_back_soon(self) -> None:
        # Long enough that a healthy picture never gets cut off, short enough
        # that one dead request cannot freeze the wall for the whole folder.
        stall = self.results["constants"]["stallMs"]
        self.assertGreaterEqual(stall, 5000)
        self.assertLessEqual(stall, 60000)


class BrowserWiringTest(unittest.TestCase):
    """The popup half of the promise: selectable immediately, both views."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = APP_SOURCE.read_text(encoding="utf-8")
        cls.styles = STYLES.read_text(encoding="utf-8")
        cls.thumbs = THUMBS_MODULE.read_text(encoding="utf-8")

    def _list_branch(self) -> str:
        match = re.search(r"if\(fileView==='list'\)\{(.*?)\n  \}\n  return <div className=\{`file-card", self.app, re.S)
        self.assertIsNotNone(match, "the media browser has no names-only list branch")
        return match.group(1)

    def test_view_mode_defaults_to_thumbnails(self) -> None:
        declared = re.search(r"const \[fileView,setFileView\]=useState<'thumbnails'\|'list'>\('(\w+)'\)", self.app)
        self.assertIsNotNone(declared, "the file area has no view mode state")
        self.assertEqual(declared.group(1), "thumbnails")

    def test_both_views_can_be_picked(self) -> None:
        self.assertIn("setFileView('thumbnails')", self.app)
        self.assertIn("setFileView('list')", self.app)
        self.assertIn("fileView==='list'?'file-list':'file-grid'", self.app)

    def test_list_view_loads_no_thumbnails(self) -> None:
        branch = self._list_branch()
        for forbidden in ("BrowserThumb", "<img ", "<video "):
            self.assertNotIn(forbidden, branch, f"the list view must not mount {forbidden.strip()}")

    def test_list_row_selects_without_waiting(self) -> None:
        branch = self._list_branch()
        # Row click selects (or opens a folder) through the same `open` helper the
        # thumbnail card's caption uses — plus an explicit Select button, so the
        # target does not depend on where the picture is on the card.
        self.assertIn("onClick={rowActivate}", branch)
        self.assertIn("open(file)", branch)
        self.assertIn("onClick={e=>{e.stopPropagation();open(file)}}", branch)
        self.assertIn("aria-pressed={isSel}", branch)

    def test_grid_card_shows_a_placeholder_instead_of_blocking(self) -> None:
        self.assertIn('if (!started) return <span className="thumb-pending"', self.app)
        self.assertIn('reserveThumbnailSlot(() => setStarted(true))', self.app)
        self.assertIn('watchViewportOnce(element, () => setNear(true))', self.app)
        # Async decoding: a megapixel JPEG must not block the click handler.
        self.assertIn('decoding="async"', self.app)

    def test_empty_files_are_not_queued_at_all(self) -> None:
        # A 0-byte card shows the "unavailable" icon right away: no request, no
        # slot — junk files cannot slow the pictures the user actually wants.
        self.assertIn("if (file.empty) return <span className=\"file-thumb-fallback\">", self.app)
        self.assertIn("export function isThumbnailFile(file: { kind?: unknown, empty?: unknown })", self.thumbs)

    def test_thumbnail_gate_ignores_icons(self) -> None:
        self.assertIn("const isPicture = isThumbnailFile(file)", self.app)
        self.assertIn("export function isThumbnailFile", self.thumbs)
        self.assertIn("export function reserveThumbnailSlot", self.thumbs)
        self.assertIn("export function watchViewportOnce", self.thumbs)

    def test_styles_layout_both_views(self) -> None:
        for selector in (".thumb-pending", ".file-toolbar", ".file-view-toggle", ".file-list", ".file-row", ".row-select"):
            self.assertIn(selector, self.styles, f"{selector} is missing from styles.css")
        # The list scrolls inside the popup exactly like the grid does, so a
        # long folder cannot push the Add button out of reach.
        self.assertIn(".file-area .file-list,.picker-body .file-list{flex:1;min-height:0;overflow-y:auto", self.styles)


if __name__ == "__main__":
    unittest.main()
