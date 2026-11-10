"""Exercise HTTP dispatch without opening a socket or a browser."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import Workbench, make_handler


class ReviewAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.workbench = Workbench(Path(self.temp.name))
        self.handler = make_handler(self.workbench).__new__(make_handler(self.workbench))
        self.responses = []
        self.handler.send = lambda status, payload, mime=None: self.responses.append((status, payload, mime))

    def tearDown(self):
        self.workbench.histories.close()
        self.workbench.reviews.close()
        self.temp.cleanup()

    def request(self, path, body=None, token=None):
        self.handler.path = path
        self.handler.headers = {"Host": "127.0.0.1:8766"}
        if body is None:
            self.handler.do_GET()
        else:
            raw = json.dumps(body).encode()
            self.handler.rfile = io.BytesIO(raw)
            self.handler.headers.update({"Content-Length": str(len(raw)), "X-Workbench-Token": token or ""})
            self.handler.do_POST()
        return self.responses[-1]

    def test_default_status_and_static_assets(self):
        code, value, _ = self.request("/api/reviews")
        self.assertEqual(code, 200)
        self.assertFalse(value["settings"]["enabled"])
        self.assertEqual(value["settings"]["time"], "19:30")
        self.assertFalse(value["migration"]["original_schedule_verified"])
        for path in ("/review.js", "/review.css", "/assets/review-room-v1.png"):
            code, value, _ = self.request(path)
            self.assertEqual(code, 200)
            self.assertTrue(value)

    def test_new_mutation_routes_keep_csrf_protection(self):
        code, _, _ = self.request("/api/reviews/settings", {"enabled": True})
        self.assertEqual(code, 403)
        self.assertFalse(self.workbench.reviews.store.settings()["enabled"])
        code, value, _ = self.request("/api/reviews/settings", {"time": "20:00"}, self.workbench.csrf)
        self.assertEqual(code, 200)
        self.assertEqual(value["time"], "20:00")
        code, _, _ = self.request("/api/reviews/generate", {"date": "2026-10-22"})
        self.assertEqual(code, 403)

    def test_generate_dispatch_is_nonblocking_and_validated(self):
        with patch.object(self.workbench.reviews, "start", return_value={"message": "已启动"}) as start:
            code, value, _ = self.request("/api/reviews/generate", {"date": "2026-10-22"}, self.workbench.csrf)
            self.assertEqual(code, 202)
            start.assert_called_once_with({"date": "2026-10-22"})
        code, _, _ = self.request("/api/reviews/settings", {"time": "09:30"}, self.workbench.csrf)
        self.assertEqual(code, 400)

    def test_artifact_dispatch_rejects_paths_and_does_not_invent_failed_copy(self):
        record = self.workbench.reviews.store.save({"trade_date": "2026-10-22", "status": "verification_failed",
            "title": "", "body": "", "sources": [], "created_at": "2026-10-22T19:30:00+08:00", "missing": []})
        code, value, mime = self.request(f"/api/reviews/artifact?id={record['id']}&name=bundle.zip")
        self.assertEqual(code, 200)
        self.assertTrue(value.startswith(b"PK"))
        self.assertEqual(mime, "application/zip")
        self.assertEqual(self.request(f"/api/reviews/artifact?id={record['id']}&name=douyin.txt")[0], 400)
        self.assertEqual(self.request("/api/reviews/report?id=../settings")[0], 400)


if __name__ == "__main__":
    unittest.main()
