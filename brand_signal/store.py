"""跨市场设备服务质量分析：store模块。"""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timezone


SCHEMA = """
CREATE TABLE IF NOT EXISTS markets(code TEXT PRIMARY KEY, timezone TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS products(code TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS devices(id TEXT PRIMARY KEY, market TEXT NOT NULL REFERENCES markets(code), product TEXT NOT NULL REFERENCES products(code), installed_at TEXT NOT NULL, retired_at TEXT, revision INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY, source TEXT NOT NULL, request_key TEXT NOT NULL, digest TEXT NOT NULL, status TEXT NOT NULL, accepted INTEGER NOT NULL DEFAULT 0, rejected INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, UNIQUE(source, request_key));
CREATE TABLE IF NOT EXISTS batch_rows(batch_id TEXT NOT NULL REFERENCES batches(id), row_no INTEGER NOT NULL, event_key TEXT, status TEXT NOT NULL, reason TEXT, payload TEXT NOT NULL, PRIMARY KEY(batch_id,row_no));
CREATE TABLE IF NOT EXISTS source_events(source TEXT NOT NULL, event_key TEXT NOT NULL, digest TEXT NOT NULL, record_id TEXT NOT NULL, PRIMARY KEY(source,event_key));
CREATE TABLE IF NOT EXISTS tickets(id TEXT PRIMARY KEY, device_id TEXT NOT NULL REFERENCES devices(id), opened_at TEXT NOT NULL, severity TEXT NOT NULL, state TEXT NOT NULL, closed_at TEXT, revision INTEGER NOT NULL DEFAULT 1, origin_batch TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ticket_history(ticket_id TEXT NOT NULL REFERENCES tickets(id), revision INTEGER NOT NULL, event_at TEXT NOT NULL, action TEXT NOT NULL, state TEXT NOT NULL, note TEXT NOT NULL, PRIMARY KEY(ticket_id,revision));
CREATE TABLE IF NOT EXISTS telemetry(id TEXT PRIMARY KEY, device_id TEXT NOT NULL REFERENCES devices(id), observed_at TEXT NOT NULL, run_hours REAL NOT NULL, downtime_hours REAL NOT NULL, origin_batch TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS revisions(id INTEGER PRIMARY KEY CHECK(id=1), value INTEGER NOT NULL);
INSERT OR IGNORE INTO revisions(id,value) VALUES(1,0);
CREATE TABLE IF NOT EXISTS candidates(id TEXT PRIMARY KEY, market TEXT NOT NULL, from_day TEXT NOT NULL, to_day TEXT NOT NULL, source_revision INTEGER NOT NULL, expected_head INTEGER NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS candidate_rows(candidate_id TEXT NOT NULL REFERENCES candidates(id), day TEXT NOT NULL, product TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(candidate_id,day,product));
CREATE TABLE IF NOT EXISTS publication_heads(market TEXT PRIMARY KEY, version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS publications(market TEXT NOT NULL, version INTEGER NOT NULL, candidate_id TEXT NOT NULL UNIQUE REFERENCES candidates(id), source_revision INTEGER NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(market,version));
CREATE TABLE IF NOT EXISTS publication_rows(market TEXT NOT NULL, version INTEGER NOT NULL, day TEXT NOT NULL, product TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(market,version,day,product));
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, action TEXT NOT NULL, subject TEXT NOT NULL, details TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS source_contracts(source TEXT NOT NULL, version INTEGER NOT NULL, effective_at TEXT NOT NULL, required_json TEXT NOT NULL, aliases_json TEXT NOT NULL, status TEXT NOT NULL, PRIMARY KEY(source,version));
CREATE TABLE IF NOT EXISTS reconciliations(id TEXT PRIMARY KEY, market TEXT NOT NULL, from_day TEXT NOT NULL, to_day TEXT NOT NULL, source_revision INTEGER NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS quality_alerts(id TEXT PRIMARY KEY, market TEXT NOT NULL, source TEXT NOT NULL, day TEXT NOT NULL, kind TEXT NOT NULL, severity TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS annotations(id TEXT PRIMARY KEY, market TEXT NOT NULL, device_id TEXT NOT NULL REFERENCES devices(id), day TEXT NOT NULL, category TEXT NOT NULL, text TEXT NOT NULL, revision INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS export_jobs(id TEXT PRIMARY KEY, market TEXT NOT NULL, version INTEGER NOT NULL, format TEXT NOT NULL, status TEXT NOT NULL, digest TEXT, payload TEXT, created_at TEXT NOT NULL, completed_at TEXT);
CREATE TABLE IF NOT EXISTS service_policies(market TEXT NOT NULL, version INTEGER NOT NULL, effective_at TEXT NOT NULL, response_hours REAL NOT NULL, closure_hours REAL NOT NULL, status TEXT NOT NULL, PRIMARY KEY(market,version));
CREATE TABLE IF NOT EXISTS service_reviews(id TEXT PRIMARY KEY, market TEXT NOT NULL, from_day TEXT NOT NULL, to_day TEXT NOT NULL, source_revision INTEGER NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS investigations(id TEXT PRIMARY KEY, market TEXT NOT NULL, alert_id TEXT NOT NULL REFERENCES quality_alerts(id), state TEXT NOT NULL, owner TEXT NOT NULL, opened_at TEXT NOT NULL, revision INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS investigation_history(investigation_id TEXT NOT NULL REFERENCES investigations(id), revision INTEGER NOT NULL, action TEXT NOT NULL, at TEXT NOT NULL, owner TEXT NOT NULL, note TEXT NOT NULL, PRIMARY KEY(investigation_id,revision));
CREATE TABLE IF NOT EXISTS market_rollups(market TEXT NOT NULL, version INTEGER NOT NULL, day TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(market,version,day));
CREATE TABLE IF NOT EXISTS source_mappings(source TEXT NOT NULL, external_id TEXT NOT NULL, device_id TEXT NOT NULL REFERENCES devices(id), valid_from TEXT NOT NULL, valid_to TEXT, PRIMARY KEY(source,external_id,valid_from));
CREATE TABLE IF NOT EXISTS providers(code TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS provider_responsibilities(id TEXT PRIMARY KEY, market TEXT NOT NULL REFERENCES markets(code), product TEXT, provider_code TEXT NOT NULL REFERENCES providers(code), valid_from TEXT NOT NULL, valid_to TEXT, revision INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ticket_reassignments(id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL REFERENCES tickets(id), event_at TEXT NOT NULL, from_provider TEXT NOT NULL REFERENCES providers(code), to_provider TEXT NOT NULL REFERENCES providers(code), reason TEXT NOT NULL, ticket_revision INTEGER NOT NULL, request_key TEXT, origin_batch TEXT, created_at TEXT NOT NULL, UNIQUE(ticket_id,event_at));
CREATE TABLE IF NOT EXISTS candidate_accountability(candidate_id TEXT NOT NULL REFERENCES candidates(id), day TEXT NOT NULL, product TEXT NOT NULL, provider TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(candidate_id,day,product,provider));
CREATE TABLE IF NOT EXISTS publication_accountability(market TEXT NOT NULL, version INTEGER NOT NULL, day TEXT NOT NULL, product TEXT NOT NULL, provider TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(market,version,day,product,provider));
CREATE INDEX IF NOT EXISTS idx_devices_market ON devices(market,product);
CREATE INDEX IF NOT EXISTS idx_tickets_device ON tickets(device_id,opened_at);
CREATE INDEX IF NOT EXISTS idx_telemetry_device ON telemetry(device_id,observed_at);
CREATE INDEX IF NOT EXISTS idx_audit_subject ON audit(subject,id);
CREATE INDEX IF NOT EXISTS idx_responsibilities_scope ON provider_responsibilities(market,product,valid_from);
CREATE INDEX IF NOT EXISTS idx_reassignments_ticket ON ticket_reassignments(ticket_id,event_at);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        db = self.connect()
        try:
            db.executescript(SCHEMA)
        finally:
            db.close()

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=15000")
        return db

    @contextmanager
    def transaction(self):
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def read(self):
        db = self.connect()
        try:
            yield db
        finally:
            db.close()

    @staticmethod
    def revision(db: sqlite3.Connection) -> int:
        return int(db.execute("SELECT value FROM revisions WHERE id=1").fetchone()[0])

    @staticmethod
    def bump(db: sqlite3.Connection) -> int:
        db.execute("UPDATE revisions SET value=value+1 WHERE id=1")
        return Store.revision(db)

    @staticmethod
    def audit(db: sqlite3.Connection, action: str, subject: str, details: dict):
        db.execute("INSERT INTO audit(at,action,subject,details) VALUES(?,?,?,?)", (now(), action, subject, json.dumps(details, sort_keys=True, ensure_ascii=False)))

    def audit_trail(self, subject: str | None = None, limit: int = 100) -> list[dict]:
        with self.read() as db:
            if subject:
                rows = db.execute("SELECT * FROM audit WHERE subject=? ORDER BY id DESC LIMIT ?", (subject, limit)).fetchall()
            else:
                rows = db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return [{**dict(r), "details": json.loads(r["details"])} for r in rows]
