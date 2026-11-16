from brand_signal.errors import DomainError
from support import Scenario


class ProviderTests(Scenario):
    def test_provider_registry_is_unique(self):
        self.providers.register_provider("P-A", "Alpha")
        with self.assertRaises(DomainError) as error:
            self.providers.register_provider("P-A", "Alpha Again")
        self.assertEqual(error.exception.code, "provider_exists")

    def test_responsibility_overlap_rejected_but_adjacent_allowed(self):
        self.providers.register_provider("P-A", "Alpha")
        self.providers.register_provider("P-B", "Beta")
        first = self.providers.add_responsibility("ES", "dryer", "P-A", "2026-10-01T00:00:00Z", "2026-10-15T00:00:00Z", 0)
        # 半开区间：与前一段重叠
        with self.assertRaises(DomainError) as error:
            self.providers.add_responsibility("ES", "dryer", "P-B", "2026-10-14T00:00:00Z", None, 1)
        self.assertEqual(error.exception.code, "responsibility_overlap")
        # 端点相接合法
        second = self.providers.add_responsibility("ES", "dryer", "P-B", "2026-10-15T00:00:00Z", None, 1)
        self.assertEqual(second["revision"], 2)
        # 不同 scope（市场级 vs 产品级）互不视为重叠
        market = self.providers.add_responsibility("ES", None, "P-A", "2026-10-01T00:00:00Z", None, 0)
        self.assertIsNone(market["product"])

    def test_responsibility_cas_and_close(self):
        first = self.seed_providers()
        with self.assertRaises(DomainError) as error:
            self.providers.add_responsibility("ES", "dryer", "P-B", "2026-11-01T00:00:00Z", None, 0)
        self.assertEqual(error.exception.code, "stale_responsibility")
        closed = self.providers.close_responsibility(first["id"], "2026-11-01T00:00:00Z", 1)
        self.assertEqual(closed["valid_to"], "2026-11-01T00:00:00+00:00")
        with self.assertRaises(DomainError) as error:
            self.providers.close_responsibility(first["id"], "2026-11-02T00:00:00Z", 2)
        self.assertEqual(error.exception.code, "responsibility_closed")

    def test_product_scope_takes_precedence_over_market(self):
        self.seed_providers()
        self.submit_ticket()
        ticket = self.service.ticket("ticket-1")
        self.assertEqual(ticket["responsibility"]["initial_provider"], "P-A")
        self.assertEqual(ticket["responsibility"]["current_provider"], "P-A")

    def test_unassigned_ticket_when_no_responsibility(self):
        self.providers.register_provider("P-A", "Alpha")
        self.submit_ticket()
        timeline = self.providers.timeline("ticket-1")
        self.assertIsNone(timeline["initial_provider"])
        self.assertIsNone(timeline["current_provider"])

    def test_reassignment_builds_non_overlapping_segments(self):
        self.seed_providers()
        self.submit_ticket()
        self.providers.reassign("ticket-1", "2026-10-21T00:00:00Z", "P-A", "P-B", "handoff", 1)
        timeline = self.providers.timeline("ticket-1")
        self.assertEqual([s["provider"] for s in timeline["segments"]], ["P-A", "P-B"])
        self.assertEqual(timeline["current_provider"], "P-B")
        ticket = self.service.ticket("ticket-1")
        self.assertEqual(len(ticket["reassignments"]), 1)
        self.assertEqual(ticket["revision"], 2)

    def test_duplicate_reassignment_at_same_time_rejected(self):
        self.seed_providers()
        self.submit_ticket()
        self.providers.reassign("ticket-1", "2026-10-21T00:00:00Z", "P-A", "P-B", "", 1)
        with self.assertRaises(DomainError) as error:
            self.providers.reassign("ticket-1", "2026-10-21T00:00:00Z", "P-B", "P-A", "", 2)
        self.assertEqual(error.exception.code, "duplicate_reassignment")

    def test_self_reassignment_rejected(self):
        self.seed_providers()
        self.submit_ticket()
        with self.assertRaises(DomainError) as error:
            self.providers.reassign("ticket-1", "2026-10-21T00:00:00Z", "P-A", "P-A", "", 1)
        self.assertEqual(error.exception.code, "self_reassignment")

    def test_from_provider_mismatch_rejected(self):
        self.seed_providers()
        self.submit_ticket()
        with self.assertRaises(DomainError) as error:
            self.providers.reassign("ticket-1", "2026-10-21T00:00:00Z", "P-B", "P-A", "", 1)
        self.assertEqual(error.exception.code, "from_provider_mismatch")

    def test_concurrent_reassignment_conflicts_on_revision(self):
        self.seed_providers()
        self.submit_ticket()
        self.providers.reassign("ticket-1", "2026-10-21T00:00:00Z", "P-A", "P-B", "", 1)
        with self.assertRaises(DomainError) as error:
            self.providers.reassign("ticket-1", "2026-10-21T01:00:00Z", "P-B", "P-A", "", 1)
        self.assertEqual(error.exception.code, "stale_ticket")

    def test_reassignment_before_open_rejected(self):
        self.seed_providers()
        self.submit_ticket()
        with self.assertRaises(DomainError) as error:
            self.providers.reassign("ticket-1", "2026-10-20T00:00:00Z", "P-A", "P-B", "", 1)
        self.assertEqual(error.exception.code, "invalid_reassignment_time")

    def test_reassignment_after_close_rejected(self):
        self.seed_providers()
        self.submit_ticket()
        self.service.transition("ticket-1", "acknowledge", "2026-10-21T00:00:00Z", 1)
        self.service.transition("ticket-1", "resolve", "2026-10-21T01:00:00Z", 2)
        self.service.transition("ticket-1", "close", "2026-10-21T02:00:00Z", 3)
        with self.assertRaises(DomainError) as error:
            self.providers.reassign("ticket-1", "2026-10-21T03:00:00Z", "P-A", "P-B", "", 4)
        self.assertEqual(error.exception.code, "reassignment_window_closed")

    def test_late_reassignment_replays_chain_by_event_time(self):
        # 先有 P-B 责任区间，工单开放后一次性补两条迟到转派，链路按事件时间重建
        self.providers.register_provider("P-A", "Alpha")
        self.providers.register_provider("P-B", "Beta")
        self.providers.add_responsibility("ES", "dryer", "P-A", "2026-10-01T00:00:00Z", None, 0)
        self.providers.add_responsibility("ES", None, "P-B", "2026-10-01T00:00:00Z", None, 0)
        self.submit_ticket(at="2026-10-20T00:00:00Z")
        self.providers.reassign("ticket-1", "2026-10-20T06:00:00Z", "P-A", "P-B", "late", 1)
        self.providers.reassign("ticket-1", "2026-10-20T12:00:00Z", "P-B", "P-A", "late back", 2)
        timeline = self.providers.timeline("ticket-1")
        self.assertEqual([s["provider"] for s in timeline["segments"]], ["P-A", "P-B", "P-A"])
        self.assertEqual(timeline["current_provider"], "P-A")

    def test_reassignment_batch_idempotent_and_per_row_results(self):
        self.seed_providers()
        self.submit_ticket()
        rows = [
            {"event_key": "k1", "ticket_id": "ticket-1", "event_at": "2026-10-21T00:00:00Z", "from_provider": "P-A", "to_provider": "P-B"},
            {"event_key": "k2", "ticket_id": "ticket-1", "event_at": "2026-10-21T01:00:00Z", "from_provider": "P-B", "to_provider": "P-B"},
        ]
        first = self.providers.submit_reassignments("req-1", rows)
        self.assertEqual((first["accepted"], first["rejected"]), (1, 1))
        self.assertEqual(first["rows"][1]["reason"], "self_reassignment")
        replay = self.providers.submit_reassignments("req-1", rows)
        self.assertTrue(replay["replay"])
        self.assertEqual(replay["id"], first["id"])
        with self.assertRaises(DomainError) as error:
            self.providers.submit_reassignments("req-1", [rows[0]])
        self.assertEqual(error.exception.code, "request_conflict")

    def test_batch_chain_reassignments_advance_revision(self):
        # 同一批次内对一张工单连续两次合法转派，修订号须逐行推进且链路完整
        self.providers.register_provider("P-A", "Alpha")
        self.providers.register_provider("P-B", "Beta")
        self.providers.add_responsibility("ES", "dryer", "P-A", "2026-10-01T00:00:00Z", None, 0)
        self.providers.add_responsibility("ES", None, "P-B", "2026-10-01T00:00:00Z", None, 0)
        self.submit_ticket()
        rows = [
            {"event_key": "k1", "ticket_id": "ticket-1", "event_at": "2026-10-21T00:00:00Z", "from_provider": "P-A", "to_provider": "P-B"},
            {"event_key": "k2", "ticket_id": "ticket-1", "event_at": "2026-10-21T01:00:00Z", "from_provider": "P-B", "to_provider": "P-A"},
        ]
        result = self.providers.submit_reassignments("chain-1", rows)
        self.assertEqual(result["accepted"], 2)
        timeline = self.providers.timeline("ticket-1")
        self.assertEqual([s["provider"] for s in timeline["segments"]], ["P-A", "P-B", "P-A"])
        self.assertEqual(self.service.ticket("ticket-1")["revision"], 3)

    def test_reassignment_bumps_source_revision(self):
        self.seed_providers()
        self.submit_ticket()
        before = self.analytics.live("ES", "2026-10-21", "2026-10-21")["source_revision"]
        self.providers.reassign("ticket-1", "2026-10-21T00:00:00Z", "P-A", "P-B", "", 1)
        after = self.analytics.live("ES", "2026-10-21", "2026-10-21")["source_revision"]
        self.assertGreater(after, before)
