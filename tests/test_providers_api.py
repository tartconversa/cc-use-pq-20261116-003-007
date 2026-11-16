import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from brand_signal.api import create_app


class ProviderApiTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.client = TestClient(create_app(str(Path(directory.name) / "http.sqlite3")))
        self.client.post("/markets", json={"code": "ES", "timezone": "Europe/Madrid"})
        self.client.post("/products", json={"code": "dryer", "name": "Dryer"})
        self.client.post("/devices", json={"id": "d1", "market": "ES", "product": "dryer", "installed_at": "2026-10-01T00:00:00Z"})
        self.client.post("/providers", json={"code": "P-A", "name": "Alpha"})
        self.client.post("/providers", json={"code": "P-B", "name": "Beta"})

    def _responsibility(self, provider, product=None, expected_latest=0):
        return self.client.post("/responsibilities", json={"market": "ES", "product": product, "provider": provider, "valid_from": "2026-10-01T00:00:00Z", "expected_latest": expected_latest})

    def _open_ticket(self):
        upload = self.client.post("/ingest/tickets", json={"request_key": "r1", "rows": [{"event_key": "e1", "id": "t1", "device_id": "d1", "opened_at": "2026-10-21T06:00:00Z"}]})
        self.assertEqual(upload.status_code, 200)

    def test_provider_and_responsibility_journey(self):
        resp = self._responsibility("P-A", "dryer")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["revision"], 1)
        # 重叠返回 409 稳定错误码
        conflict = self._responsibility("P-B", "dryer", expected_latest=1)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["error"], "responsibility_overlap")
        listing = self.client.get("/responsibilities/ES", params={"product": "dryer"})
        self.assertEqual(len(listing.json()), 1)

    def test_reassignment_and_responsibility_timeline(self):
        self._responsibility("P-A", "dryer")
        self._responsibility("P-B", None)
        self._open_ticket()
        ok = self.client.post("/tickets/t1/reassignments", json={"event_at": "2026-10-21T10:00:00Z", "from_provider": "P-A", "to_provider": "P-B", "reason": "handoff", "expected_revision": 1})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()["revision"], 2)
        # 并发修订冲突
        stale = self.client.post("/tickets/t1/reassignments", json={"event_at": "2026-10-21T11:00:00Z", "from_provider": "P-B", "to_provider": "P-A", "expected_revision": 1})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["error"], "stale_ticket")
        timeline = self.client.get("/tickets/t1/responsibility").json()
        self.assertEqual([s["provider"] for s in timeline["segments"]], ["P-A", "P-B"])
        # 工单详情内嵌转派与责任链
        detail = self.client.get("/tickets/t1").json()
        self.assertEqual(len(detail["reassignments"]), 1)
        self.assertEqual(detail["responsibility"]["current_provider"], "P-B")

    def test_reassignment_batch_replay(self):
        self._responsibility("P-A", "dryer")
        self._responsibility("P-B", None)
        self._open_ticket()
        body = {"request_key": "rb-1", "rows": [
            {"event_key": "k1", "ticket_id": "t1", "event_at": "2026-10-21T10:00:00Z", "from_provider": "P-A", "to_provider": "P-B"},
            {"event_key": "k2", "ticket_id": "t1", "event_at": "2026-10-21T11:00:00Z", "from_provider": "P-B", "to_provider": "P-B"},
        ]}
        first = self.client.post("/reassignments", json=body)
        self.assertEqual(first.json()["accepted"], 1)
        self.assertEqual(first.json()["rejected"], 1)
        replay = self.client.post("/reassignments", json=body)
        self.assertTrue(replay.json()["replay"])

    def test_live_and_published_accountability(self):
        self._responsibility("P-A", "dryer")
        self._open_ticket()
        live = self.client.get("/accountability/ES", params={"from_day": "2026-10-21", "to_day": "2026-10-21"})
        self.assertEqual(live.status_code, 200)
        self.assertEqual(live.json()["rows"][0]["provider"], "P-A")
        revision = live.json()["source_revision"]
        candidate = self.client.post("/candidates", json={"market": "ES", "from_day": "2026-10-21", "to_day": "2026-10-21", "expected_head": 0}).json()
        self.client.post(f"/candidates/{candidate['id']}/publish", json={"expected_head": 0, "expected_source_revision": revision})
        published = self.client.get("/publications/ES/1/accountability")
        self.assertEqual(published.status_code, 200)
        self.assertEqual(published.json()["rows"][0]["provider"], "P-A")
        filtered = self.client.get("/publications/ES/1/accountability", params={"provider": "P-A"})
        self.assertTrue(all(r["provider"] == "P-A" for r in filtered.json()["rows"]))

    def test_unknown_provider_is_404(self):
        response = self.client.post("/responsibilities", json={"market": "ES", "provider": "NOPE", "valid_from": "2026-10-01T00:00:00Z", "expected_latest": 0})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"], "unknown_provider")
