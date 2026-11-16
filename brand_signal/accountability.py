"""跨市场设备服务质量分析：accountability模块。

按市场、产品和责任方分析工单的响应、结案与积压。每张工单先重放责任链得到互不
重叠的持有时间段，事件归属于事件发生时刻的持有方，持有时间按自然日裁剪，因此
跨责任边界的工单在任何指标里都不会被重复计入。
"""
from collections import defaultdict
from .analytics import days_between, overlap_hours
from .catalog import Catalog, instant, market_day
from .errors import require
from .policies import Policies
from .providers import Providers
from .store import Store


UNASSIGNED = "__unassigned__"


class Accountability:
    def __init__(self, store: Store):
        self.store = store

    def calculate(self, db, market: str, from_day: str, to_day: str) -> list[dict]:
        zone_row = db.execute("SELECT timezone FROM markets WHERE code=?", (market,)).fetchone()
        require(zone_row is not None, "unknown_market", "market unknown", 404)
        zone = zone_row["timezone"]
        days = list(days_between(from_day, to_day))
        tickets = db.execute("SELECT t.*,d.product,d.market FROM tickets t JOIN devices d ON d.id=t.device_id WHERE d.market=? ORDER BY t.id", (market,)).fetchall()
        history = defaultdict(list)
        for row in db.execute("SELECT h.* FROM ticket_history h JOIN tickets t ON t.id=h.ticket_id JOIN devices d ON d.id=t.device_id WHERE d.market=? ORDER BY h.ticket_id,h.revision", (market,)):
            history[row["ticket_id"]].append(row)
        group = {}

        def bucket(day: str, product: str, provider: str | None) -> dict:
            key = (day, product, provider)
            if key not in group:
                group[key] = {
                    "day": day, "market": market, "product": product,
                    "provider": provider,
                    "holding_hours": 0.0, "responses": 0, "response_hours_total": 0.0,
                    "response_overdue": 0, "closures": 0, "closure_hours_total": 0.0,
                    "closure_overdue": 0, "transfers_in": 0, "transfers_out": 0,
                    "open_at_end": 0, "open_overdue_at_end": 0,
                }
            return group[key]

        for ticket in tickets:
            opened_day = market_day(ticket["opened_at"], zone)
            timeline = Providers._timeline(db, market, ticket["product"], ticket)
            actions = history[ticket["id"]]
            first_ack = next((a for a in actions if a["action"] == "acknowledge"), None)
            first_close = next((a for a in actions if a["action"] == "close"), None)
            policy = Policies.applicable(db, market, ticket["opened_at"])
            for day in days:
                day_start, day_end = Catalog.local_bounds(day, zone)
                # 持有时间：责任片段与自然日相交，按片段持有方裁剪，天然不重复
                for segment in timeline["segments"]:
                    if instant(segment["start"]) >= day_end:
                        continue
                    seg_end = instant(segment["end"]) if segment["end"] else None
                    if seg_end is not None and seg_end <= day_start:
                        continue
                    hours = overlap_hours(instant(segment["start"]), seg_end if seg_end else day_end, day_start, day_end)
                    if hours:
                        bucket(day, ticket["product"], segment["provider"])["holding_hours"] += hours
                # 响应归 ack 时刻持有方
                if first_ack is not None and market_day(first_ack["event_at"], zone) == day:
                    row = bucket(day, ticket["product"], Providers.holder_at(timeline, first_ack["event_at"]))
                    row["responses"] += 1
                    elapsed = (instant(first_ack["event_at"]) - instant(ticket["opened_at"])).total_seconds() / 3600
                    row["response_hours_total"] += elapsed
                    if policy is not None and elapsed > policy["response_hours"]:
                        row["response_overdue"] += 1
                # 结案归 close 时刻持有方
                if first_close is not None and market_day(first_close["event_at"], zone) == day:
                    row = bucket(day, ticket["product"], Providers.holder_at(timeline, first_close["event_at"]))
                    row["closures"] += 1
                    elapsed = (instant(first_close["event_at"]) - instant(ticket["opened_at"])).total_seconds() / 3600
                    row["closure_hours_total"] += elapsed
                    if policy is not None and elapsed > policy["closure_hours"]:
                        row["closure_overdue"] += 1
                # 积压：日末仍开放，归日末持有方
                if instant(ticket["opened_at"]) < day_end:
                    latest = "open"
                    for action in actions:
                        if instant(action["event_at"]) < day_end:
                            latest = action["state"]
                    if latest not in {"closed", "cancelled"}:
                        holder = Providers.holder_at(timeline, day_end.isoformat())
                        # 日末落在最后一个开放片段内（末片段 end 为空或晚于日末）
                        row = bucket(day, ticket["product"], holder)
                        row["open_at_end"] += 1
                        if policy is not None:
                            age_hours = (day_end - instant(ticket["opened_at"])).total_seconds() / 3600
                            if age_hours > policy["closure_hours"]:
                                row["open_overdue_at_end"] += 1
                # 转派按事件当日分别计入转出/转入方
                for move in timeline["reassignments"]:
                    if market_day(move["event_at"], zone) != day:
                        continue
                    bucket(day, ticket["product"], move["from_provider"])["transfers_out"] += 1
                    bucket(day, ticket["product"], move["to_provider"])["transfers_in"] += 1
        rows = []
        for row in group.values():
            row["holding_hours"] = round(row["holding_hours"], 6)
            row["response_hours_total"] = round(row["response_hours_total"], 6)
            row["closure_hours_total"] = round(row["closure_hours_total"], 6)
            row["avg_response_hours"] = round(row["response_hours_total"] / row["responses"], 6) if row["responses"] else None
            row["avg_closure_hours"] = round(row["closure_hours_total"] / row["closures"], 6) if row["closures"] else None
            row["response_on_time_rate"] = (row["responses"] - row["response_overdue"]) / row["responses"] if row["responses"] else None
            row["closure_on_time_rate"] = (row["closures"] - row["closure_overdue"]) / row["closures"] if row["closures"] else None
            rows.append(row)
        rows.sort(key=lambda item: (item["day"], item["product"], item["provider"] or ""))
        return rows

    def live(self, market: str, from_day: str, to_day: str) -> dict:
        with self.store.read() as db:
            return {"source_revision": self.store.revision(db), "rows": self.calculate(db, market, from_day, to_day)}
