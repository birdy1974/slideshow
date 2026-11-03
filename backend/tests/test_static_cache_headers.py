"""The SPA shell is revalidated, so a deployment is actually seen.

Everything Vite rebuilds is named after a hash of its contents, so a new image
cannot collide with an old bundle — but `index.html` is the file that *lists*
those names, and its URL never changes while its meaning changes with every
release. `FileResponse`/`StaticFiles` send no `Cache-Control` at all, which
leaves freshness to the browser's own arithmetic (a common heuristic is "10 % of
the age I saw when I cached it"), so an old shell can survive several deploys and
the app a person gets is the previous build: same URL, same looks, none of the
features. That is how "it was merged but I do not see it" usually happens.

The fix is one header on the two responses the SPA fallback returns —
`no-cache`, meaning *ask before using it*, which costs a `304` and a few
kilobytes — and deliberately nothing on `/assets/*`, whose names already say what
they contain. These are source checks rather than HTTP ones: this suite runs
without `httpx`, and what could regress here is the policy on a two-line route,
not Starlette's machinery. The behaviour was verified against a running server
(the shell and `/fonts/*.ttf` answer `cache-control: no-cache`, a hashed asset
answers none). See *A new release does not appear in the browser* in
`docs/synology-ds918.md`.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MAIN = REPO / "backend" / "app" / "main.py"
INDEX_HTML = REPO / "dist" / "index.html"


def _spa_source() -> str:
    """The fallback route, from its `def` to the end of its indented block. It is
    defined inside `if static_dir.exists():`, so the indentation is part of it."""
    lines = MAIN.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.lstrip().startswith("def spa("))
    indent = len(lines[start]) - len(lines[start].lstrip())
    body = [lines[start]]
    for line in lines[start + 1:]:
        if line.strip() and (len(line) - len(line.lstrip())) <= indent:
            break
        body.append(line)
    return "\n".join(body)


class SpaShellCachingTest(unittest.TestCase):
    def test_the_shell_is_revalidated_and_not_forbidden(self) -> None:
        body = _spa_source()
        returns = re.findall(r"return FileResponse\((.*?)\)\n", body + "\n", re.S)
        self.assertEqual(len(returns), 2, "the file branch and the index.html fallback")
        for each in returns:
            self.assertIn("headers=SHELL_HEADERS", each,
                          f"both responses must carry the policy, or one path stays cacheable: {each.strip()}")
        policy = re.search(r"SHELL_HEADERS = \{([^}]*)\}", MAIN.read_text(encoding="utf-8"))
        self.assertIsNotNone(policy, "the policy is stated once, next to the mount that shares it")
        self.assertIn('"Cache-Control": "no-cache"', policy.group(1))
        self.assertNotIn("no-store", policy.group(1),
                         "no-store would throw away the 304 fast path for a file the browser already holds")
        self.assertNotIn("max-age", policy.group(1),
                         "any freshness window is the bug itself: a licence to serve the old shell")

    def test_replaced_files_are_revalidated_so_a_fix_is_visible(self) -> None:
        """The sibling of the shell bug, on the other kind of URL.

        Six responses hand the browser a file that can be replaced while its URL
        stays the same: the media streamer (a movie re-uploaded under its own name),
        the movie proxy and the filmstrip sprite (both regenerated on the next
        request), the two gallery-clip routes (which now re-render a truncated clip
        in place), and a job's own output file. With no `Cache-Control` a browser
        applies its own freshness arithmetic to those, and then "I rendered again
        and got exactly the same broken result" is literally true — the app served
        new bytes and the browser never asked.

        `no-cache`, not `no-store`: the 304 fast path and range requests survive.
        """
        source = MAIN.read_text(encoding="utf-8")
        policy = re.search(r"REVALIDATED_HEADERS = \{([^}]*)\}", source)
        self.assertIsNotNone(policy, "the policy for replaceable files is stated once, next to the shell's")
        self.assertIn('"Cache-Control": "no-cache"', policy.group(1))
        self.assertNotIn("no-store", policy.group(1))
        self.assertNotIn("max-age", policy.group(1), "any freshness window is the bug itself")
        self.assertEqual(7, source.count("headers=REVALIDATED_HEADERS"),
                         "the media streamer, the movie proxy, the filmstrip sprite, both gallery routes and the job file — "
                         "one fewer and a replaced file is cacheable again")

    def test_hashed_assets_keep_their_names_as_their_cache_key(self) -> None:
        text = MAIN.read_text(encoding="utf-8")
        mount = re.search(r'app\.mount\("/assets",\s*StaticFiles\(([^)]*)\)', text)
        self.assertIsNotNone(mount, "the hashed bundles are served by a mount, not by the fallback")
        self.assertNotIn("SHELL_HEADERS", mount.group(1),
                         "revalidating an immutable file on every navigation is pure cost")

    def test_the_policy_is_not_spilled_onto_the_api(self) -> None:
        text = MAIN.read_text(encoding="utf-8")
        self.assertEqual(text.count("headers=SHELL_HEADERS"), 2,
                         "it belongs on the shell alone — probes, media streaming and job files have their own reasons")

    @unittest.skipUnless(INDEX_HTML.exists(), "needs `npm run build` (CI builds before it tests)")
    def test_the_served_shell_points_at_hashed_payload(self) -> None:
        """The promise the whole policy rests on: the shell is small and must be
        asked for, and everything it references is named after what it contains."""
        html = INDEX_HTML.read_text(encoding="utf-8")
        refs = re.findall(r'(?:src|href)="/assets/([^"]+)"', html)
        self.assertTrue(refs, "a shell with no hashed payload would make revalidation pointless")
        for name in refs:
            self.assertRegex(name, r"^[\w.-]+-[A-Za-z0-9_-]{6,}\.(?:js|css)$",
                             f"{name} is not content-addressed, so it belongs with the shell, not in /assets")

    @unittest.skipUnless(INDEX_HTML.exists(), "needs `npm run build` (CI builds before it tests)")
    def test_the_built_shell_does_not_still_mask_karaoke_glyphs(self) -> None:
        """The regression this whole mechanism exists to prevent, checked at the
        artifact level: whatever the browser is served, the CSS it links must be
        the one where the sung word is revealed by a clip on x — not a gradient
        clipped to the text, which is what cut the tops and bottoms off."""
        html = INDEX_HTML.read_text(encoding="utf-8")
        css = [name for name in re.findall(r'href="/assets/([^"]+)"', html) if name.endswith(".css")]
        self.assertTrue(css, "no stylesheet linked")
        text = (INDEX_HTML.parent / "assets" / css[0]).read_text(encoding="utf-8")
        self.assertIn(".kara-word", text, "the karaoke word rule is missing entirely")
        self.assertNotIn("background-clip", text,
                         "clipping paint to the glyphs is a mask, and a mask on a word box cuts letters")


if __name__ == "__main__":
    unittest.main()
