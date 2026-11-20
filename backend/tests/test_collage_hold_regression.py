"""Timing semantics for background holds and independent lane edits."""
import json
import subprocess
import unittest
from pathlib import Path

from app.collage import collage_duration, collage_exit_end, exit_start, exit_fly_length, photo_state

ROOT = Path(__file__).resolve().parents[2]


class HoldRegression(unittest.TestCase):
    def spec(self):
        return {"photos": [{"path": "a", "at": 0, "until": 8},
                           {"path": "b", "delay": 1}, {"path": "c", "delay": 1, "exit": "none"}],
                "animation": "drop", "exit": "fade", "hold": 0}

    def test_hold_follows_last_exit_and_does_not_shift_exits(self):
        spec = self.spec()
        stops = [exit_start(spec, i) + exit_fly_length(spec, i) for i in (0, 1)]
        self.assertEqual(collage_duration(spec), 8)
        self.assertEqual(collage_exit_end(spec), 8)
        for hold in (0, 1, 3):
            spec["hold"] = hold
            self.assertEqual(collage_duration(spec), 8 + hold)
            self.assertEqual(stops, [exit_start(spec, i) + exit_fly_length(spec, i) for i in (0, 1)])
            self.assertEqual(photo_state(spec, 2, 8.01)["alpha"], 0)

    def test_lane_writes_preserve_other_windows_and_group_offsets(self):
        script = """
import assert from 'node:assert/strict';
import {photoWindow, writePhotoWindow, collageDuration} from './src/collageCore.ts';
let spec = JSON.parse(process.argv[1]);
const before = spec.photos.map((_, i) => photoWindow(spec, i));
function write(i, lane, n) { spec = {...spec, photos: writePhotoWindow(spec, i, lane, String(n))}; }
write(0, 'until', 10);
assert.deepEqual(photoWindow(spec, 1), before[1]);
assert.deepEqual(photoWindow(spec, 2), before[2]);
assert.equal(collageDuration(spec), 10);
// Simulate the lane's separate writes for a group body drag.
write(0, 'at', before[0].from + 1); write(0, 'until', before[0].to + 1);
write(1, 'at', before[1].from + 1); write(1, 'until', before[1].to + 1);
assert.deepEqual(photoWindow(spec, 0), {from: before[0].from + 1, to: before[0].to + 1});
assert.deepEqual(photoWindow(spec, 1), {from: before[1].from + 1, to: before[1].to + 1});
assert.deepEqual(photoWindow(spec, 2), before[2]);
"""
        subprocess.run(['node', '--experimental-strip-types', '--input-type=module', '-e', script,
                        json.dumps(self.spec())], cwd=ROOT, check=True, capture_output=True, text=True)
