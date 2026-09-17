import asyncio
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from tools.field_dashboard import DashboardHandler, EventHub


class FakeApp:
    def __init__(self, loop):
        self.loop = loop
        self.hub = EventHub()
        self.requests = []

    def snapshot(self):
        return {"mode": "OBSERVE", "processes": []}

    def record(self, event, **_kwargs):
        pass

    async def action(self, path, body):
        self.requests.append((path, body))
        if path == "/api/fail":
            raise ValueError("blocked by test")
        return {"ok": True, "path": path}


class DashboardHttpTests(unittest.TestCase):
    def setUp(self):
        from http.server import ThreadingHTTPServer

        self.loop = asyncio.new_event_loop()
        self.loop_thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.loop_thread.start()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardHandler)
        self.server.app = FakeApp(self.loop)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.loop_thread.join(timeout=2)

    def test_snapshot_and_frontend_are_served(self):
        with urlopen(self.base + "/api/snapshot", timeout=3) as response:
            self.assertEqual(json.load(response)["mode"], "OBSERVE")
        with urlopen(self.base + "/", timeout=3) as response:
            self.assertIn("RoboGame 联调台", response.read().decode("utf-8"))

    def test_post_reaches_controller_and_validation_error_is_409(self):
        request = Request(self.base + "/api/test", data=b'{"value": 1}', headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=3) as response:
            self.assertTrue(json.load(response)["ok"])
        self.assertEqual(self.server.app.requests[-1], ("/api/test", {"value": 1}))

        request = Request(self.base + "/api/fail", data=b"{}", method="POST")
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=3)
        self.assertEqual(caught.exception.code, 409)
        self.assertIn("blocked by test", caught.exception.read().decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
