import tempfile
import unittest
from pathlib import Path
from brand_signal.catalog import Catalog
from brand_signal.intake import Intake
from brand_signal.service import Service
from brand_signal.telemetry import Telemetry
from brand_signal.analytics import Analytics
from brand_signal.publication import Publication
from brand_signal.providers import Providers
from brand_signal.accountability import Accountability
from brand_signal.store import Store


class Scenario(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "data.sqlite3"
        self.store = Store(self.path)
        self.catalog = Catalog(self.store)
        self.intake = Intake(self.store)
        self.service = Service(self.store)
        self.telemetry = Telemetry(self.store)
        self.analytics = Analytics(self.store)
        self.publication = Publication(self.store)
        self.providers = Providers(self.store)
        self.accountability = Accountability(self.store)
        self.catalog.add_market("ES", "Europe/Madrid")
        self.catalog.add_product("dryer", "Synthetic Dryer")
        self.catalog.install("device-1", "ES", "dryer", "2026-10-01T00:00:00Z")

    def seed_providers(self):
        """注册两个服务商并建立 dryer 产品级责任 P-A，市场级兜底 P-B。"""
        self.providers.register_provider("P-A", "Alpha")
        self.providers.register_provider("P-B", "Beta")
        self.providers.add_responsibility("ES", None, "P-B", "2026-10-01T00:00:00Z", None, 0)
        return self.providers.add_responsibility("ES", "dryer", "P-A", "2026-10-01T00:00:00Z", None, 0)

    def ticket(self, key="event-1", identifier="ticket-1", at="2026-10-20T22:30:00Z", device="device-1", severity="normal"):
        return {"event_key": key, "id": identifier, "device_id": device, "opened_at": at, "severity": severity}

    def meter(self, key="meter-key", identifier="meter-1", at="2026-10-21T08:00:00Z", device="device-1", run=12, down=2):
        return {"event_key": key, "id": identifier, "device_id": device, "observed_at": at, "run_hours": run, "downtime_hours": down}

    def submit_ticket(self, **changes):
        return self.intake.submit("tickets", "tickets-" + str(changes.get("key", "1")), [self.ticket(**changes)])

    def submit_meter(self, **changes):
        return self.intake.submit("telemetry", "meters-" + str(changes.get("key", "1")), [self.meter(**changes)])

    def release(self, day="2026-10-21", expected_head=0):
        candidate = self.publication.prepare("ES", day, day, expected_head)
        return self.publication.publish(candidate["id"], expected_head, candidate["source_revision"])