from brand_signal.errors import DomainError
from brand_signal.policies import Policies
from support import Scenario


class AccountabilityTests(Scenario):
    def _two_provider_handoff(self):
        """工单在本地 10-21 由 P-A 打开，ack 在转派前归 A；10:00Z 转 P-B 后 close 归 B。"""
        self.providers.register_provider("P-A", "Alpha")
        self.providers.register_provider("P-B", "Beta")
        self.providers.add_responsibility("ES", "dryer", "P-A", "2026-10-01T00:00:00Z", None, 0)
        self.providers.add_responsibility("ES", None, "P-B", "2026-10-01T00:00:00Z", None, 0)
        self.submit_ticket(at="2026-10-21T06:00:00Z")
        self.service.transition("ticket-1", "acknowledge", "2026-10-21T08:00:00Z", 1)
        self.providers.reassign("ticket-1", "2026-10-21T10:00:00Z", "P-A", "P-B", "handoff", 2)
        self.service.transition("ticket-1", "resolve", "2026-10-21T12:00:00Z", 3)
        self.service.transition("ticket-1", "close", "2026-10-21T13:00:00Z", 4)

    def test_response_and_closure_follow_holder_at_event_time(self):
        self._two_provider_handoff()
        rows = self.accountability.live("ES", "2026-10-21", "2026-10-21")["rows"]
        by_provider = {row["provider"]: row for row in rows}
        self.assertEqual(by_provider["P-A"]["responses"], 1)
        self.assertEqual(by_provider["P-A"]["closures"], 0)
        self.assertEqual(by_provider["P-B"]["responses"], 0)
        self.assertEqual(by_provider["P-B"]["closures"], 1)

    def test_holding_hours_split_without_double_count(self):
        self._two_provider_handoff()
        rows = self.accountability.live("ES", "2026-10-21", "2026-10-21")["rows"]
        total = sum(row["holding_hours"] for row in rows)
        # 工单 06:00Z 打开、13:00Z 关闭，共 7 小时；10:00Z 转派前后分别 4h / 3h，不重复
        self.assertAlmostEqual(total, 7.0, places=6)
        by_provider = {row["provider"]: row for row in rows}
        self.assertAlmostEqual(by_provider["P-A"]["holding_hours"], 4.0, places=6)
        self.assertAlmostEqual(by_provider["P-B"]["holding_hours"], 3.0, places=6)

    def test_transfers_counted_for_both_sides_once(self):
        self._two_provider_handoff()
        rows = self.accountability.live("ES", "2026-10-21", "2026-10-21")["rows"]
        by_provider = {row["provider"]: row for row in rows}
        self.assertEqual(by_provider["P-A"]["transfers_out"], 1)
        self.assertEqual(by_provider["P-B"]["transfers_in"], 1)

    def test_backlog_uses_end_of_day_holder(self):
        self.providers.register_provider("P-A", "Alpha")
        self.providers.register_provider("P-B", "Beta")
        self.providers.add_responsibility("ES", "dryer", "P-A", "2026-10-01T00:00:00Z", None, 0)
        self.providers.add_responsibility("ES", None, "P-B", "2026-10-01T00:00:00Z", None, 0)
        self.submit_ticket(at="2026-10-21T10:00:00Z")
        self.providers.reassign("ticket-1", "2026-10-21T12:00:00Z", "P-A", "P-B", "", 1)
        rows = self.accountability.live("ES", "2026-10-21", "2026-10-21")["rows"]
        by_provider = {row["provider"]: row for row in rows}
        # 日末仍开放且持有方为 P-B，整张工单只在 P-B 计一次积压
        self.assertEqual(by_provider["P-B"]["open_at_end"], 1)
        self.assertEqual(by_provider["P-A"]["open_at_end"], 0)
        self.assertEqual(sum(row["open_at_end"] for row in rows), 1)

    def test_overdue_uses_policy_against_holder_metrics(self):
        self.providers.register_provider("P-A", "Alpha")
        self.providers.add_responsibility("ES", "dryer", "P-A", "2026-10-01T00:00:00Z", None, 0)
        Policies(self.store).register("ES", "2026-10-01T00:00:00Z", 1, 2, 0)
        self.submit_ticket(at="2026-10-21T00:00:00Z")
        self.service.transition("ticket-1", "acknowledge", "2026-10-21T05:00:00Z", 1)
        row = self.accountability.live("ES", "2026-10-21", "2026-10-21")["rows"][0]
        self.assertEqual(row["response_overdue"], 1)
        self.assertEqual(row["response_on_time_rate"], 0.0)
        self.assertIsNone(row["avg_closure_hours"])

    def test_unassigned_bucket_uses_null_provider(self):
        self.providers.register_provider("P-A", "Alpha")
        self.submit_ticket(at="2026-10-21T00:00:00Z")
        row = self.accountability.live("ES", "2026-10-21", "2026-10-21")["rows"][0]
        self.assertIsNone(row["provider"])
        self.assertEqual(row["open_at_end"], 1)

    def test_backlog_counted_once_per_day_across_range(self):
        self.providers.register_provider("P-A", "Alpha")
        self.providers.add_responsibility("ES", "dryer", "P-A", "2026-10-01T00:00:00Z", None, 0)
        self.submit_ticket(at="2026-10-20T12:00:00Z")
        rows = self.accountability.live("ES", "2026-10-20", "2026-10-22")["rows"]
        # 三天里每天末都开放，恰好三次积压，且都归同一持有方，无重复
        self.assertEqual(sum(r["open_at_end"] for r in rows), 3)
        self.assertTrue(all(r["provider"] == "P-A" for r in rows))

    def test_cancelled_ticket_excluded_from_backlog(self):
        self.providers.register_provider("P-A", "Alpha")
        self.providers.add_responsibility("ES", "dryer", "P-A", "2026-10-01T00:00:00Z", None, 0)
        self.submit_ticket(at="2026-10-21T00:00:00Z")
        self.service.transition("ticket-1", "cancel", "2026-10-21T01:00:00Z", 1)
        row = self.accountability.live("ES", "2026-10-21", "2026-10-21")["rows"][0]
        self.assertEqual(row["open_at_end"], 0)


class AccountabilityPublicationTests(Scenario):
    def _handoff(self):
        self.providers.register_provider("P-A", "Alpha")
        self.providers.register_provider("P-B", "Beta")
        self.providers.add_responsibility("ES", "dryer", "P-A", "2026-10-01T00:00:00Z", None, 0)
        self.providers.add_responsibility("ES", None, "P-B", "2026-10-01T00:00:00Z", None, 0)
        self.submit_ticket(at="2026-10-20T22:30:00Z")

    def test_candidate_snapshots_accountability_rows(self):
        self._handoff()
        candidate = self.publication.prepare("ES", "2026-10-21", "2026-10-21", 0)
        self.assertGreater(candidate["accountability_rows"], 0)

    def test_published_accountability_is_immutable_after_late_reassignment(self):
        self._handoff()
        first = self.release("2026-10-21")
        before = self.publication.read_accountability("ES", first["version"])
        # 迟到转派（仍在开放工单上，事件时间在过去）
        self.providers.reassign("ticket-1", "2026-10-21T05:00:00Z", "P-A", "P-B", "late", 1)
        second = self.release("2026-10-21", expected_head=1)
        self.assertEqual(self.publication.read_accountability("ES", first["version"]), before)
        after = self.publication.read_accountability("ES", second["version"])
        providers_v2 = {row["provider"] for row in after["rows"]}
        self.assertIn("P-B", providers_v2)

    def test_late_reassignment_invalidates_prepared_candidate(self):
        self._handoff()
        candidate = self.publication.prepare("ES", "2026-10-21", "2026-10-21", 0)
        self.providers.reassign("ticket-1", "2026-10-21T05:00:00Z", "P-A", "P-B", "late", 1)
        self.assertFalse(self.publication.candidate(candidate["id"])["ready"])
        with self.assertRaises(DomainError) as error:
            self.publication.publish(candidate["id"], 0, candidate["source_revision"])
        self.assertEqual(error.exception.code, "stale_source")

    def test_published_accountability_filter_by_provider(self):
        self._handoff()
        result = self.release("2026-10-21")
        only_a = self.publication.read_accountability("ES", result["version"], provider="P-A")
        self.assertTrue(only_a["rows"])
        self.assertTrue(all(row["provider"] == "P-A" for row in only_a["rows"]))

    def test_existing_metric_rows_unchanged_by_accountability(self):
        self._handoff()
        result = self.release("2026-10-21")
        release = self.publication.read("ES", result["version"])
        # 原有按产品的指标行结构保持不变
        self.assertEqual(set(release["rows"][0].keys()) & {"day", "product", "ticket_opened", "ticket_open_at_end"}, {"day", "product", "ticket_opened", "ticket_open_at_end"})
        self.assertNotIn("provider", release["rows"][0])
