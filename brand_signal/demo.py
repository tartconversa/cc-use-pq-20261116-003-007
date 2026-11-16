"""跨市场设备服务质量分析：demo模块。"""
import json
import tempfile
from pathlib import Path
from .analytics import Analytics
from .catalog import Catalog
from .intake import Intake
from .publication import Publication
from .providers import Providers
from .service import Service
from .store import Store


def run() -> dict:
    with tempfile.TemporaryDirectory() as directory:
        store = Store(Path(directory) / "demo.sqlite3")
        catalog = Catalog(store)
        intake = Intake(store)
        service = Service(store)
        publication = Publication(store)
        catalog.add_market("ES", "Europe/Madrid")
        catalog.add_product("dryer", "Synthetic Drying Unit")
        catalog.install("unit-1", "ES", "dryer", "2026-10-01T08:00:00Z")
        batch = intake.submit("tickets", "service-upload-1", [{"event_key": "ticket-1", "id": "case-1", "device_id": "unit-1", "opened_at": "2026-10-20T22:30:00Z", "severity": "critical"}, {"event_key": "bad-1", "id": "case-bad", "device_id": "missing", "opened_at": "2026-10-20T22:30:00Z"}])
        assert batch["accepted"] == 1 and batch["rejected"] == 1
        assert intake.submit("tickets", "service-upload-1", [{"event_key": "ticket-1", "id": "case-1", "device_id": "unit-1", "opened_at": "2026-10-20T22:30:00Z", "severity": "critical"}, {"event_key": "bad-1", "id": "case-bad", "device_id": "missing", "opened_at": "2026-10-20T22:30:00Z"}])["replay"]
        service.transition("case-1", "acknowledge", "2026-10-21T00:30:00Z", 1)
        service.transition("case-1", "resolve", "2026-10-21T02:00:00Z", 2)
        service.transition("case-1", "close", "2026-10-21T03:00:00Z", 3)
        intake.submit("telemetry", "meter-upload-1", [{"event_key": "meter-1", "id": "observation-1", "device_id": "unit-1", "observed_at": "2026-10-21T05:00:00Z", "run_hours": 15, "downtime_hours": 2}])
        candidate = publication.prepare("ES", "2026-10-21", "2026-10-21", 0)
        first = publication.publish(candidate["id"], 0, candidate["source_revision"])
        before = publication.read("ES", 1)["rows"]
        intake.submit("telemetry", "meter-upload-2", [{"event_key": "meter-2", "id": "observation-2", "device_id": "unit-1", "observed_at": "2026-10-21T10:00:00Z", "run_hours": 2, "downtime_hours": 1}])
        next_candidate = publication.prepare("ES", "2026-10-21", "2026-10-21", 1)
        second = publication.publish(next_candidate["id"], 1, next_candidate["source_revision"])
        after = publication.read("ES", 1)["rows"]
        assert before == after
        delta = publication.diff("ES", 1, 2)["changes"]
        assert delta and delta[0]["before"]["telemetry_events"] == 1 and delta[0]["after"]["telemetry_events"] == 2

        # 服务商责任：登记带生效区间的责任关系，转派工单并验证归属与版本不变性
        providers = Providers(store)
        providers.register_provider("P-A", "Alpha Local Partner")
        providers.register_provider("P-B", "Beta Local Partner")
        providers.add_responsibility("ES", "dryer", "P-A", "2026-10-01T00:00:00Z", None, 0)
        providers.add_responsibility("ES", None, "P-B", "2026-10-01T00:00:00Z", None, 0)
        intake.submit("tickets", "service-upload-2", [{"event_key": "ticket-2", "id": "case-2", "device_id": "unit-1", "opened_at": "2026-10-23T06:00:00Z", "severity": "high"}])
        assert providers.timeline("case-2")["initial_provider"] == "P-A"
        providers.reassign("case-2", "2026-10-23T10:00:00Z", "P-A", "P-B", "cross-provider handoff", 1)
        service.transition("case-2", "acknowledge", "2026-10-23T11:00:00Z", 2)
        candidate3 = publication.prepare("ES", "2026-10-23", "2026-10-23", 2)
        third = publication.publish(candidate3["id"], 2, candidate3["source_revision"])
        accountability_v3 = publication.read_accountability("ES", third["version"])
        before_v3 = json.loads(json.dumps(accountability_v3))
        # 迟到转派：工单仍开放，补一条更早事件时间的记录后重放责任链
        providers.reassign("case-2", "2026-10-23T07:00:00Z", "P-A", "P-B", "late handoff record", 3)
        candidate4 = publication.prepare("ES", "2026-10-23", "2026-10-23", 3)
        fourth = publication.publish(candidate4["id"], 3, candidate4["source_revision"])
        assert publication.read_accountability("ES", third["version"]) == before_v3
        accountability_v4 = publication.read_accountability("ES", fourth["version"])
        providers_v4 = sorted({row["provider"] for row in accountability_v4["rows"] if row["provider"]})
        assert providers_v4 == ["P-A", "P-B"]
        return {"accepted": batch["accepted"], "rejected": batch["rejected"], "versions": [first["version"], second["version"]], "old_unchanged": before == after, "changed_rows": len(delta), "accountability_versions": [third["version"], fourth["version"]], "accountability_immutable": publication.read_accountability("ES", third["version"]) == before_v3, "source_revision": Analytics(store).live("ES", "2026-10-21", "2026-10-21")["source_revision"]}


if __name__ == "__main__":
    print(json.dumps(run(), sort_keys=True))
