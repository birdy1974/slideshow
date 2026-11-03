"""The media browser must read the mount once per change, not once per render.

There is no JS test runner in this repository, so — like the other frontend pins
here — these assertions read `src/App.tsx`. They are narrow on purpose: each one
names a line whose *shape* decides whether the open popup keeps refetching. The
bug was invisible in every other layer: `timeline={timelinePictures(media)}` is a
plain function call in JSX, so the prop was a new array on every parent render; the
browser memoizes its storyline listing on that prop and its fetch effect depends on
the memo, so each render scheduled another `/api/media/browse`, which set
`loading`, which re-rendered. On a NAS mount the user watched the folder list jump
up and down while "reading…" blinked, and the picker stayed slow.

`timeline=[]` as a default parameter was the same trap for the plain "docker mount
media" popup, which passes no storyline at all: a default is re-evaluated per
render, so that popup looped too — and the `…reading…` suffix plus the
"Reading mounted folder…" banner *inserting a row above the grid* is what turned
the loop into visible motion.
"""
from pathlib import Path
import unittest

APP = Path("src/App.tsx").read_text(encoding="utf-8") if Path("src/App.tsx").exists() else Path("../src/App.tsx").read_text(encoding="utf-8")


class MediaBrowserStabilityTest(unittest.TestCase):
    def assertIn(self, member, container=None, msg=None) -> None:
        super().assertIn(member, container if container is not None else APP, msg)

    def test_the_storyline_prop_is_memoized_at_the_source(self) -> None:
        self.assertIn("const timelinePicturesForBrowser = useMemo(() => timelinePictures(media), [media])")
        self.assertIn("timeline={timelinePicturesForBrowser}")
        self.assertNotIn("timeline={timelinePictures(media)}", APP,
                         "a call in JSX is a new array per render, and that array is a memo key")

    def test_the_default_prop_is_a_shared_constant(self) -> None:
        self.assertIn("const NO_TIMELINE_SLIDES: TimelinePicture[] = []")
        self.assertIn("timeline=NO_TIMELINE_SLIDES }", APP)
        self.assertNotIn("timeline=[] }", APP, "a default `[]` is fresh per render, which is enough to loop")

    def test_the_listing_memo_keys_on_content_not_identity(self) -> None:
        key_at = APP.find("const timelineKey=(timeline||[]).map(")
        self.assertGreater(key_at, -1, "the key must be built from every field the rows read")
        memo_at = APP.find("const timelineEntries=useMemo(", key_at)
        self.assertGreater(memo_at, key_at, "the key has to be declared before the memo that uses it")
        # The memo's own window: from its opening to the next declaration after it.
        block = APP[key_at:key_at + 1400]
        self.assertIn("},[timelineKey])", block, "the memo must depend on the content key")
        self.assertNotIn("},[timeline])", block, "keying on the array identity re-runs the fetch on every parent render")
        for field in ("rootName", "path", "name", "note"):
            self.assertRegex(block, rf"p\.{field}\b", f"a row reads p.{field}, so the key must contain it")

    def test_a_background_refresh_does_not_reflow_the_grid(self) -> None:
        self.assertIn("{loading&&!shown.length&&<div className=\"browser-info\">", APP,
                      "the banner takes a row; showing it while the folder is already listed is the jump")


if __name__ == "__main__":
    unittest.main()
