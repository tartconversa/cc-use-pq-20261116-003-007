"""跨市场设备服务质量分析：providers模块。

服务商责任关系使用半开生效区间 [valid_from, valid_to)；产品级责任优先于市场级
（product 为空）责任。转派记录携带事件时间，工单责任链按事件时间重放得到，
任何时刻只有一个责任方，跨责任边界不会产生重复归属。
"""
import hashlib
import json
import uuid
from .catalog import Catalog, iso, instant
from .errors import DomainError, require
from .store import Store, now


REASSIGNMENT_SOURCE = "reassignments"


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class Providers:
    def __init__(self, store: Store):
        self.store = store

    # ------------------------------------------------------------------ 服务商
    def register_provider(self, code: str, name: str) -> dict:
        require(isinstance(code, str) and 2 <= len(code) <= 48 and code.replace("-", "").isalnum(), "invalid_provider", "provider code required")
        require(isinstance(name, str) and 1 <= len(name.strip()) <= 120, "invalid_provider", "provider name required")
        with self.store.transaction() as db:
            require(not db.execute("SELECT 1 FROM providers WHERE code=?", (code,)).fetchone(), "provider_exists", "provider already exists", 409)
            db.execute("INSERT INTO providers VALUES(?,?,?)", (code, name.strip(), now()))
            self.store.audit(db, "provider_registered", code, {"name": name.strip()})
            return {"code": code, "name": name.strip()}

    def list_providers(self) -> list[dict]:
        with self.store.read() as db:
            return [dict(row) for row in db.execute("SELECT * FROM providers ORDER BY code")]

    @staticmethod
    def provider(db, code: str):
        row = db.execute("SELECT * FROM providers WHERE code=?", (code,)).fetchone()
        require(row is not None, "unknown_provider", "provider unknown", 404)
        return row

    # -------------------------------------------------------------- 责任生效区间
    @staticmethod
    def _scope_latest(db, market: str, product: str | None) -> int:
        row = db.execute("SELECT coalesce(max(revision),0) AS latest FROM provider_responsibilities WHERE market=? AND product IS ?", (market, product)).fetchone()
        return int(row["latest"])

    def add_responsibility(self, market: str, product: str | None, provider_code: str, valid_from: str, valid_to: str | None, expected_latest: int) -> dict:
        require(product is None or (isinstance(product, str) and product), "invalid_product", "product code required")
        require(type(expected_latest) is int and expected_latest >= 0, "invalid_version", "expected latest scope revision required")
        start = iso(valid_from)
        stop = iso(valid_to) if valid_to else None
        require(stop is None or instant(stop) > instant(start), "invalid_interval", "responsibility end must follow start")
        identifier = uuid.uuid4().hex
        with self.store.transaction() as db:
            require(db.execute("SELECT 1 FROM markets WHERE code=?", (market,)).fetchone(), "unknown_market", "market unknown", 404)
            if product is not None:
                require(db.execute("SELECT 1 FROM products WHERE code=?", (product,)).fetchone(), "unknown_product", "product unknown", 404)
            self.provider(db, provider_code)
            latest = self._scope_latest(db, market, product)
            require(latest == expected_latest, "stale_responsibility", "responsibility scope changed", 409)
            existing = db.execute("SELECT * FROM provider_responsibilities WHERE market=? AND product IS ?", (market, product)).fetchall()
            new_start = instant(start)
            new_stop = instant(stop) if stop else None
            for row in existing:
                old_start = instant(row["valid_from"])
                old_stop = instant(row["valid_to"]) if row["valid_to"] else None
                # 半开区间重叠：新起点早于旧终点，且（新终点开放或晚于旧起点）
                starts_before_old_end = old_stop is None or new_start < old_stop
                ends_after_old_start = new_stop is None or new_stop > old_start
                require(not (starts_before_old_end and ends_after_old_start), "responsibility_overlap", "responsibility interval already covered in scope", 409)
            revision = latest + 1
            db.execute("INSERT INTO provider_responsibilities VALUES(?,?,?,?,?,?,?,?)", (identifier, market, product, provider_code, start, stop, revision, now()))
            source_revision = self.store.bump(db)
            self.store.audit(db, "responsibility_added", identifier, {"market": market, "product": product, "provider": provider_code, "valid_from": start, "valid_to": stop, "source_revision": source_revision})
            return {"id": identifier, "market": market, "product": product, "provider": provider_code, "valid_from": start, "valid_to": stop, "revision": revision}

    def close_responsibility(self, responsibility_id: str, valid_to: str, expected_revision: int) -> dict:
        stop = iso(valid_to)
        with self.store.transaction() as db:
            row = db.execute("SELECT * FROM provider_responsibilities WHERE id=?", (responsibility_id,)).fetchone()
            require(row is not None, "unknown_responsibility", "responsibility unknown", 404)
            require(row["revision"] == expected_revision, "stale_responsibility", "responsibility revision changed", 409)
            require(row["valid_to"] is None, "responsibility_closed", "responsibility interval already closed", 409)
            require(instant(stop) > instant(row["valid_from"]), "invalid_interval", "responsibility end must follow start")
            db.execute("UPDATE provider_responsibilities SET valid_to=?,revision=? WHERE id=?", (stop, expected_revision + 1, responsibility_id))
            source_revision = self.store.bump(db)
            self.store.audit(db, "responsibility_closed", responsibility_id, {"valid_to": stop, "source_revision": source_revision})
            return {"id": responsibility_id, "market": row["market"], "product": row["product"], "provider": row["provider_code"], "valid_from": row["valid_from"], "valid_to": stop, "revision": expected_revision + 1}

    def list_responsibilities(self, market: str, product: str | None = None) -> list[dict]:
        with self.store.read() as db:
            require(db.execute("SELECT 1 FROM markets WHERE code=?", (market,)).fetchone(), "unknown_market", "market unknown", 404)
            if product is None:
                rows = db.execute("SELECT * FROM provider_responsibilities WHERE market=? ORDER BY product,valid_from,id", (market,))
            else:
                rows = db.execute("SELECT * FROM provider_responsibilities WHERE market=? AND product IS ? ORDER BY valid_from,id", (market, product))
            return [dict(row) for row in rows]

    @staticmethod
    def responsibility_at(db, market: str, product: str, at: str):
        """返回时刻 at 生效的责任行：产品级优先，其次市场级；区间为半开 [from,to)。"""
        timestamp = iso(at)
        row = db.execute(
            "SELECT * FROM provider_responsibilities WHERE market=? AND product=? AND valid_from<=? AND (valid_to IS NULL OR valid_to>?) ORDER BY valid_from DESC,id DESC LIMIT 1",
            (market, product, timestamp, timestamp),
        ).fetchone()
        if row is None:
            row = db.execute(
                "SELECT * FROM provider_responsibilities WHERE market=? AND product IS NULL AND valid_from<=? AND (valid_to IS NULL OR valid_to>?) ORDER BY valid_from DESC,id DESC LIMIT 1",
                (market, timestamp, timestamp),
            ).fetchone()
        return row

    # ------------------------------------------------------------------ 转派记录
    def _validate_reassignment(self, db, ticket_id: str, event_at: str, from_provider: str, to_provider: str) -> dict:
        require(isinstance(ticket_id, str) and ticket_id, "invalid_ticket_id", "ticket id required")
        ticket = db.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
        require(ticket is not None, "unknown_ticket", "ticket unknown", 404)
        require(ticket["state"] != "cancelled", "ticket_not_active", "cancelled ticket cannot be reassigned", 409)
        moment = iso(event_at)
        require(instant(moment) >= instant(ticket["opened_at"]), "invalid_reassignment_time", "reassignment precedes ticket opening")
        if ticket["closed_at"] is not None:
            require(instant(moment) < instant(ticket["closed_at"]), "reassignment_window_closed", "reassignment must precede ticket closure", 409)
        device = Catalog.device(db, ticket["device_id"])
        require(isinstance(from_provider, str) and from_provider, "invalid_provider", "from provider required")
        require(isinstance(to_provider, str) and to_provider, "invalid_provider", "to provider required")
        require(from_provider != to_provider, "self_reassignment", "reassignment needs a different provider")
        self.provider(db, from_provider)
        self.provider(db, to_provider)
        require(not db.execute("SELECT 1 FROM ticket_reassignments WHERE ticket_id=? AND event_at=?", (ticket_id, moment)).fetchone(), "duplicate_reassignment", "ticket already reassigned at this time", 409)
        holder = self._holder_before(db, device["market"], device["product"], ticket, moment)
        require(holder is not None, "ticket_unassigned", "ticket has no responsible provider at that time", 409)
        require(holder == from_provider, "from_provider_mismatch", "from provider is not the responsible provider at that time", 409)
        target = db.execute(
            "SELECT 1 FROM provider_responsibilities WHERE market=? AND (product IS NULL OR product=?) AND provider_code=? AND valid_from<=? AND (valid_to IS NULL OR valid_to>?) LIMIT 1",
            (device["market"], device["product"], to_provider, moment, moment),
        ).fetchone()
        require(target is not None, "responsibility_gap", "to provider has no active responsibility at that time", 409)
        return {"ticket": ticket, "device": device, "at": moment}

    def _holder_before(self, db, market: str, product: str, ticket, moment: str) -> str | None:
        """按事件时间重放责任链，返回 moment 之前（不含等于）的责任方。"""
        initial = self.responsibility_at(db, market, product, ticket["opened_at"])
        holder = initial["provider_code"] if initial else None
        rows = db.execute("SELECT * FROM ticket_reassignments WHERE ticket_id=? AND event_at<? ORDER BY event_at,id", (ticket["id"], moment)).fetchall()
        for row in rows:
            holder = row["to_provider"]
        return holder

    def reassign(self, ticket_id: str, event_at: str, from_provider: str, to_provider: str, reason: str, expected_revision: int) -> dict:
        require(isinstance(reason, str) and len(reason) <= 1000, "invalid_note", "reason too long")
        require(type(expected_revision) is int and expected_revision > 0, "invalid_revision", "positive ticket revision required")
        with self.store.transaction() as db:
            ticket = db.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
            require(ticket is not None, "unknown_ticket", "ticket unknown", 404)
            require(ticket["revision"] == expected_revision, "stale_ticket", "ticket revision changed", 409)
            checked = self._validate_reassignment(db, ticket_id, event_at, from_provider, to_provider)
            return self._insert_reassignment(db, ticket_id, checked["at"], from_provider, to_provider, reason, expected_revision + 1, None, None)

    def _insert_reassignment(self, db, ticket_id: str, moment: str, from_provider: str, to_provider: str, reason: str, ticket_revision: int, request_key: str | None, batch_id: str | None) -> dict:
        identifier = uuid.uuid4().hex
        db.execute("UPDATE tickets SET revision=? WHERE id=?", (ticket_revision, ticket_id))
        db.execute("INSERT INTO ticket_reassignments(id,ticket_id,event_at,from_provider,to_provider,reason,ticket_revision,request_key,origin_batch,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (identifier, ticket_id, moment, from_provider, to_provider, reason, ticket_revision, request_key, batch_id, now()))
        source_revision = self.store.bump(db)
        self.store.audit(db, "ticket_reassigned", ticket_id, {"id": identifier, "from": from_provider, "to": to_provider, "at": moment, "source_revision": source_revision})
        return {"id": identifier, "ticket_id": ticket_id, "event_at": moment, "from_provider": from_provider, "to_provider": to_provider, "reason": reason, "revision": ticket_revision}

    def submit_reassignments(self, request_key: str, rows: list[dict]) -> dict:
        """批量转派渠道：请求键幂等，逐行给出接收或拒绝的确定结果。"""
        require(isinstance(request_key, str) and 1 <= len(request_key) <= 100, "invalid_request_key", "request key required")
        require(isinstance(rows, list) and 1 <= len(rows) <= 500, "invalid_batch", "batch needs 1..500 rows")
        try:
            body_digest = digest(rows)
        except (TypeError, ValueError, OverflowError) as exc:
            raise DomainError("invalid_batch", "batch must contain JSON values") from exc
        with self.store.transaction() as db:
            prior = db.execute("SELECT * FROM batches WHERE source=? AND request_key=?", (REASSIGNMENT_SOURCE, request_key)).fetchone()
            if prior:
                require(prior["digest"] == body_digest, "request_conflict", "request key used with different payload", 409)
                result = self._batch_result(db, prior["id"])
                result["replay"] = True
                return result
            batch_id = uuid.uuid4().hex
            db.execute("INSERT INTO batches(id,source,request_key,digest,status,created_at) VALUES(?,?,?,?,?,?)", (batch_id, REASSIGNMENT_SOURCE, request_key, body_digest, "processing", now()))
            seen = set()
            for index, row in enumerate(rows):
                event_key = row.get("event_key") if isinstance(row, dict) else None
                status, reason_out = "accepted", None
                db.execute("SAVEPOINT row_input")
                try:
                    require(isinstance(row, dict), "invalid_row", "row object required")
                    require(isinstance(event_key, str) and 1 <= len(event_key) <= 100, "invalid_event_key", "event key required")
                    require(event_key not in seen, "duplicate_in_batch", "event key repeated in batch")
                    seen.add(event_key)
                    checked = self._validate_reassignment(db, row.get("ticket_id"), row.get("event_at"), row.get("from_provider"), row.get("to_provider"))
                    current = db.execute("SELECT revision FROM tickets WHERE id=?", (row["ticket_id"],)).fetchone()["revision"]
                    saved = self._insert_reassignment(db, row["ticket_id"], checked["at"], row["from_provider"], row["to_provider"], str(row.get("reason") or ""), current + 1, request_key, batch_id)
                    db.execute("RELEASE row_input")
                except DomainError as exc:
                    db.execute("ROLLBACK TO row_input")
                    db.execute("RELEASE row_input")
                    status, reason_out = "rejected", exc.code
                db.execute("INSERT INTO batch_rows VALUES(?,?,?,?,?,?)", (batch_id, index, event_key, status, reason_out, canonical(row)))
            accepted = db.execute("SELECT count(*) FROM batch_rows WHERE batch_id=? AND status='accepted'", (batch_id,)).fetchone()[0]
            db.execute("UPDATE batches SET status='completed',accepted=?,rejected=? WHERE id=?", (accepted, len(rows) - accepted, batch_id))
            self.store.audit(db, "reassignment_batch_completed", batch_id, {"accepted": accepted, "rejected": len(rows) - accepted})
            result = self._batch_result(db, batch_id)
            result["replay"] = False
            return result

    @staticmethod
    def _batch_result(db, batch_id: str) -> dict:
        batch = dict(db.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone())
        batch["rows"] = [dict(row) for row in db.execute("SELECT row_no,event_key,status,reason FROM batch_rows WHERE batch_id=? ORDER BY row_no", (batch_id,))]
        return batch

    # ------------------------------------------------------------------ 责任时间线
    def timeline(self, ticket_id: str) -> dict:
        with self.store.read() as db:
            ticket = db.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
            require(ticket is not None, "unknown_ticket", "ticket unknown", 404)
            device = Catalog.device(db, ticket["device_id"])
            return self._timeline(db, device["market"], device["product"], ticket)

    @staticmethod
    def _timeline(db, market: str, product: str, ticket) -> dict:
        initial = Providers.responsibility_at(db, market, product, ticket["opened_at"])
        moves = [dict(row) for row in db.execute("SELECT * FROM ticket_reassignments WHERE ticket_id=? ORDER BY event_at,id", (ticket["id"],)).fetchall()]
        segments = []
        holder = initial["provider_code"] if initial else None
        boundary = ticket["opened_at"]
        for move in moves:
            segments.append({"provider": holder, "start": boundary, "end": move["event_at"]})
            holder = move["to_provider"]
            boundary = move["event_at"]
        segments.append({"provider": holder, "start": boundary, "end": ticket["closed_at"]})
        current = holder
        return {"ticket_id": ticket["id"], "market": market, "product": product, "initial_provider": segments[0]["provider"] if segments else None, "current_provider": current, "segments": segments, "reassignments": moves}

    @staticmethod
    def holder_at(timeline: dict, moment: str) -> str | None:
        """返回 moment 时刻的持有方。责任片段为半开 [start,end)：转派时刻归新
        持有方；若 moment 恰好等于工单关闭时刻（末段结束端），归最后持有方。
        按自然日裁剪的持有时间仍使用半开区间，二者互不影响。"""
        point = instant(moment)
        fallback = None
        for segment in timeline["segments"]:
            start = instant(segment["start"])
            end = instant(segment["end"]) if segment["end"] else None
            if start <= point and (end is None or point < end):
                return segment["provider"]
            if end is not None and point == end:
                fallback = segment["provider"]
        return fallback