from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.database import Database


class DatabaseRoundTripTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "slideshow.db")
        self.db.initialize()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def payload(self) -> dict:
        return {
            "schemaVersion": 1,
            "project": {"name": "Complete project", "randomOrder": True, "futureProjectFlag": "retained"},
            "media": [{
                "id": 17, "name": "one.jpg", "path": "/photos/holiday", "src": "", "type": "image",
                "duration": 7.5, "effect": "Ken Burns · Pan left", "transition": "Circle crop", "transitionTime": 1.3,
                "text": "Caption", "textMode": "overlay", "textStart": 1.0, "textEnd": 6.0,
                "textEnter": "Slide up", "textExit": "Dissolve", "textEnterDuration": .4, "textExitDuration": .7,
                "textX": 37, "textY": 82, "frameBackground": "#123456", "futureItemSetting": {"x": 1},
            }],
            "textDefaults": {"fontFamily": "Roboto", "fontSize": 54, "fontColor": "#ffeeaa", "bold": True, "italic": True, "underline": False},
            "soundtrack": {"policy": "Loop & trim", "volume": 72, "fadeOut": True, "tracks": [{"id": 2, "name": "song.mp3", "path": "/music/set", "duration": "3:20", "gain": -2}]},
            "output": {"resolution": "Full HD · 1080p", "frameRate": "30 fps", "bitrate": "8 Mbps · High", "encoder": "Auto · Quick Sync", "path": "/output", "filename": "movie"},
            "timeline": {"rows": "3", "zoom": 1.8},
            "unknownFutureSection": {"must": "survive"},
        }

    def test_create_and_update_are_lossless(self) -> None:
        payload = self.payload()
        created = self.db.save_project(payload)
        project_id = created["id"]
        for key, value in payload.items():
            self.assertEqual(value, created[key])
        self.assertEqual(1, created["revision"])

        payload["media"][0]["text"] = "Updated"
        payload["output"]["filename"] = "new-name"
        updated = self.db.save_project(payload, project_id)
        self.assertEqual(2, updated["revision"])
        self.assertEqual("Updated", updated["media"][0]["text"])
        self.assertEqual("new-name", updated["output"]["filename"])
        self.assertEqual({"must": "survive"}, updated["unknownFutureSection"])

        with self.db.connect() as conn:
            media = conn.execute("SELECT * FROM media_items WHERE project_id=?", (project_id,)).fetchone()
            self.assertEqual("Ken Burns · Pan left", media["effect"])
            self.assertEqual("Slide up", media["text_enter"])
            audio = conn.execute("SELECT * FROM audio_tracks WHERE project_id=?", (project_id,)).fetchone()
            self.assertEqual("song.mp3", audio["name"])

    def test_transaction_rolls_back_on_missing_project(self) -> None:
        with self.assertRaises(KeyError):
            self.db.save_project(self.payload(), 999)
        self.assertEqual([], self.db.list_projects())


if __name__ == "__main__":
    unittest.main()
