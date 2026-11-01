# The random entrance roll and the transition bridge's spec-side rules.
#
# The shuffle itself is TypeScript (src/collageCore.ts), because it writes
# *materialised* ids onto the photos and the engines then have nothing to agree
# about — so its invariants are checked by running the real module through node,
# the way the twin does. What the Python side owes is kept here: accepting a
# bridged name as an entrance, resolving it to the same animation, honouring the
# direction the name carries, and not dropping the shuffle's bookkeeping on save.
import json
import shutil
import subprocess
import unittest
from pathlib import Path

from app.collage import (ANIM_FAMILIES, ANIM_IDS, BRIDGE, normalize_collage, photo_anim,
                         photo_anim_name, photo_reveal_dir, resolve_anim)

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "collage_shuffle.mjs"
HAVE_NODE = shutil.which("node") is not None

from app.collage import collage_duration, collage_graph

_N = [0]


def _graph(anim: str, anim_dir: int = 0) -> str:
    """The whole filter graph for a one-photo collage running `anim`.

    Sized at 640x360 with no frame: big enough that the mask ladder's buckets do
    not collapse into one step, small enough that the strings compare without
    normalising anything away."""
    item = {"collage": {
        "layout": "grid", "animation": anim, "shape": "4:3", "seed": 1, "hold": 1.5,
        "animDir": anim_dir, "frame": {"shape": "none", "shadow": False},
        "photos": [{"path": "/a.jpg", "anim": anim, "animDir": anim_dir}],
    }}
    spec = normalize_collage(item)
    assert spec is not None
    lines, _last = collage_graph(item, 640, 360, 25, collage_duration(spec), 0, 1, "cb")
    return "".join(lines)


def _photo(**kw):
    # a unique `path` is what makes a photo usable at all — the validator drops the
    # ones without it, and a spec with no photos left normalises to None
    _N[0] += 1
    p = {"path": f"/media/IMG_{_N[0]:03d}.jpg", "name": f"IMG_{_N[0]:03d}", "w": 800, "h": 600}
    p.update(kw)
    return p


def _norm(photos, cols=1, rows=1, **kw):
    """Normalise a top-level `type: collage` item, as the API hands one over."""
    return normalize_collage({"type": "collage", "layout": "grid", "cols": cols, "rows": rows,
                              "duration": 4, "photos": photos, **kw})


class BridgeSpecTest(unittest.TestCase):
    def test_bridged_names_are_accepted_and_resolve(self) -> None:
        for name in BRIDGE:
            spec = _norm([_photo()], animation=name)
            self.assertEqual(spec["animation"], name, f"{name} dropped by the validator")
            self.assertEqual(photo_anim(spec, 0), BRIDGE[name]["target"], name)
            self.assertEqual(resolve_anim(name), BRIDGE[name]["target"], name)

    def test_unknown_name_is_not_an_animation(self) -> None:
        spec = _norm([_photo()], animation="dissolvelike")
        self.assertEqual(spec["animation"], "drop")
        self.assertNotIn("dissolvelike", ANIM_IDS)

    def test_photo_level_override_keeps_the_name(self) -> None:
        spec = _norm([_photo(anim="wiperight"), _photo()], cols=2, rows=1, animation="drop")
        self.assertEqual(photo_anim_name(spec, 0), "wiperight")
        self.assertEqual(photo_anim(spec, 0), "wipeRight")
        # …and the other photo still follows the collage
        self.assertEqual(photo_anim(spec, 1), "drop")

    def test_a_name_carries_its_own_direction(self) -> None:
        # `wipetl` is defined as the diagonal running towards the top-left corner;
        # the user's mirrored choice cannot quietly redefine what the name means.
        spec = _norm([_photo(anim="wipetl", animDir=0)])
        self.assertEqual(photo_reveal_dir(spec, 0), 1)
        # the slides, by contrast, pick a row that already travels the right way
        # (a translate has no axis for `animDir` to flip), so no forcing is involved
        self.assertEqual(resolve_anim("slideleft"), "fromRight")
        self.assertEqual(resolve_anim("slideright"), "fromLeft")
        both = _norm([_photo(anim="wipetl", animDir=0), _photo(anim="curtain", animDir=0)], cols=2)
        # a name without its own meaning inherits as usual
        self.assertEqual(photo_reveal_dir(both, 1), 0)

    def test_a_bridge_renders_exactly_like_its_target(self) -> None:
        """An alias is a *name*, not a second implementation.

        Every bridged entry must emit the same filter graph as the animation it
        resolves to — same mask, same translate, same colour chain — because the
        moment the two differ the name is a different feature with a friendlier
        label, and the label is what the user picked."""
        for name, row in BRIDGE.items():
            # a name that states a direction is that direction: the target with the
            # mirroring it asks for, which is the only way `wipetl` can mean what
            # it says on a system where nobody has to remember the flag
            dir_forced = int(row.get("dir") or 0)
            mine = _graph(name)
            theirs = _graph(row["target"], anim_dir=dir_forced)
            self.assertEqual(mine, theirs, f"{name} does not render like {row['target']}")

    def test_a_direction_forcing_name_lands_on_the_mirrored_target(self) -> None:
        # `wipetl` is the diagonal wipe *towards the top-left*: the graph has to
        # come out as the mirrored target, which is the whole content of the
        # promise the label makes.
        forced = {n: r for n, r in BRIDGE.items() if r.get("dir") == 1}
        self.assertTrue(forced, "no name forces a direction any more? update these tests")
        for name, row in forced.items():
            self.assertEqual(_graph(name), _graph(row["target"], anim_dir=1), f"{name} is not its target mirrored")
            self.assertNotEqual(_graph(name), _graph(row["target"], anim_dir=0),
                                f"{name} ignores the direction its own name states")

    def test_an_unusable_seed_is_dropped_rather_than_guessed(self) -> None:
        for junk in ("", None, [], {}, "0x10"):
            self.assertNotIn("animSeed", _norm([_photo()], animSeed=junk), repr(junk))
        for bad in (True, False):
            self.assertNotIn("animSeed", _norm([_photo()], animSeed=bad), "a bool is not a seed")
        self.assertEqual(_norm([_photo()], animSeed="7")["animSeed"], 7)


class ShuffleBookkeepingTest(unittest.TestCase):
    def test_the_roll_survives_a_save(self) -> None:
        spec = _norm([_photo(anim="iris", animLock=True), _photo(anim="glide")], cols=2,
                     animSeed=1234, animFamilies=["mask", "nonsense", "paper"], animOneOfEach=True)
        self.assertEqual(spec["animSeed"], 1234)
        self.assertEqual(spec["animFamilies"], ["mask", "paper"], "an unknown family must go, the rest stay")
        self.assertTrue(spec["animOneOfEach"])
        self.assertTrue(spec["photos"][0]["animLock"])
        self.assertNotIn("animLock", spec["photos"][1], "a photo that was not locked gains one")

    def test_families_must_be_a_list_of_names(self) -> None:
        for junk in ("mask", {"a": 1}, [], ["nonsense"]):
            self.assertNotIn("animFamilies", _norm([_photo()], animFamilies=junk), repr(junk))
        # a list with one bad entry keeps the good ones, in the order the user
        # ticked them — dropping the field because of one stale name would undo a
        # choice they can see in the panel
        self.assertEqual(_norm([_photo()], animFamilies=["mask", 3, "paper"])["animFamilies"],
                         ["mask", "paper"])
        # and the seed is read the same way here as TypeScript reads it
        self.assertNotIn("animSeed", _norm([_photo()], animSeed="0x10"), "hex is not a number here")
        self.assertEqual(_norm([_photo()], animSeed="7.9")["animSeed"], 7)
        # …and folded into the 31 bits TypeScript's validator folds to, so a roll
        # replayed after a save uses the same seed it was made with
        self.assertEqual(_norm([_photo()], animSeed=3000000000)["animSeed"], 3000000000 & 0x7FFFFFFF)
        self.assertEqual(_norm([_photo()], animSeed=-5)["animSeed"], (-5) & 0x7FFFFFFF)
        self.assertEqual(list(ANIM_FAMILIES), ["classic", "paper", "colour", "mask", "ffmpeg"])


@unittest.skipUnless(HAVE_NODE, "node is not installed")
class ShuffleRollTest(unittest.TestCase):
    """The roll's own promises, checked in the module that computes it."""

    @classmethod
    def setUpClass(cls) -> None:
        proc = subprocess.run(["node", "--experimental-strip-types", str(SCRIPT)],
                              capture_output=True, text=True, timeout=120, cwd=HERE)
        if proc.returncode != 0:
            raise AssertionError(f"shuffle script failed:\n{proc.stderr}\n{proc.stdout}")
        cls.flags = json.loads(proc.stdout)

    def test_every_promise_holds(self) -> None:
        broken = [k for k, v in self.flags.items() if v is not True]
        self.assertEqual([], broken, f"shuffle invariants failed: {broken}")


if __name__ == "__main__":
    unittest.main()
