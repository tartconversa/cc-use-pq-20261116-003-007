"""跨市场设备服务质量分析：api模块。"""
import os
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field
from .analytics import Analytics
from .catalog import Catalog
from .errors import DomainError
from .intake import Intake
from .publication import Publication
from .service import Service
from .store import Store
from .telemetry import Telemetry
from .contracts import Contracts
from .reconciliation import Reconciliation
from .alerts import Alerts
from .annotations import Annotations
from .exports import Exports
from .policies import Policies
from .reviews import Reviews
from .comparison import Comparison
from .investigations import Investigations
from .lineage import Lineage
from .rollups import Rollups
from .sampling import Sampling
from .mappings import Mappings
from .providers import Providers
from .accountability import Accountability


class MarketIn(BaseModel):
    code: str
    timezone: str


class ProductIn(BaseModel):
    code: str
    name: str


class DeviceIn(BaseModel):
    id: str
    market: str
    product: str
    installed_at: str


class RetireIn(BaseModel):
    retired_at: str
    expected_revision: int


class BatchIn(BaseModel):
    request_key: str
    rows: list[dict | list | str | int | float | None]


class TransitionIn(BaseModel):
    action: str
    at: str
    expected_revision: int
    note: str = ""


class CandidateIn(BaseModel):
    market: str
    from_day: str
    to_day: str
    expected_head: int


class PublishIn(BaseModel):
    expected_head: int
    expected_source_revision: int


class ContractIn(BaseModel):
    effective_at: str
    required: list[str]
    aliases: dict[str, str] = Field(default_factory=dict)
    expected_latest: int


class RevisionIn(BaseModel):
    expected_source_revision: int


class AnnotationIn(BaseModel):
    market: str
    device_id: str
    day: str
    category: str
    text: str


class AnnotationReplaceIn(BaseModel):
    text: str
    expected_revision: int


class ExportIn(BaseModel):
    market: str
    version: int
    format: str


class PolicyIn(BaseModel):
    effective_at: str
    response_hours: float
    closure_hours: float
    expected_latest: int


class CompareIn(BaseModel):
    selections: list[dict]
    product: str
    from_day: str
    to_day: str


class InvestigationIn(BaseModel):
    alert_id: str
    owner: str


class InvestigationTransitionIn(BaseModel):
    action: str
    expected_revision: int
    owner: str
    note: str = ""


class SampleIn(BaseModel):
    market: str
    version: int
    size: int
    seed: str


class MappingIn(BaseModel):
    external_id: str
    device_id: str
    valid_from: str
    valid_to: str | None = None


class ProviderIn(BaseModel):
    code: str
    name: str


class ResponsibilityIn(BaseModel):
    market: str
    product: str | None = None
    provider: str
    valid_from: str
    valid_to: str | None = None
    expected_latest: int


class CloseResponsibilityIn(BaseModel):
    valid_to: str
    expected_revision: int


class ReassignmentIn(BaseModel):
    event_at: str
    from_provider: str
    to_provider: str
    reason: str = ""
    expected_revision: int


class ReassignmentBatchIn(BaseModel):
    request_key: str
    rows: list[dict]


def create_app(path: str | None = None) -> FastAPI:
    db = Store(path or os.environ.get("BRAND_SIGNAL_DB", "/tmp/brand-signal.sqlite3"))
    catalog = Catalog(db)
    intake = Intake(db)
    service = Service(db)
    telemetry = Telemetry(db)
    analytics = Analytics(db)
    publication = Publication(db)
    contracts = Contracts(db)
    reconciliation = Reconciliation(db)
    alerts = Alerts(db)
    annotations = Annotations(db)
    exports = Exports(db)
    policies = Policies(db)
    reviews = Reviews(db)
    comparison = Comparison(db)
    investigations = Investigations(db)
    lineage = Lineage(db)
    rollups = Rollups(db)
    sampling = Sampling(db)
    mappings = Mappings(db)
    providers = Providers(db)
    accountability = Accountability(db)
    app = FastAPI(title="Brand Signal Service Analytics", version="1.0")
    app.state.store = db

    @app.exception_handler(DomainError)
    async def domain_error(_: Request, exc: DomainError):
        return JSONResponse(status_code=exc.status, content={"error": exc.code, "message": exc.message})

    @app.post("/markets")
    def add_market(body: MarketIn):
        return catalog.add_market(body.code, body.timezone)

    @app.get("/markets/{code}")
    def get_market(code: str):
        return catalog.market(code)

    @app.post("/products")
    def add_product(body: ProductIn):
        return catalog.add_product(body.code, body.name)

    @app.post("/devices")
    def add_device(body: DeviceIn):
        return catalog.install(body.id, body.market, body.product, body.installed_at)

    @app.get("/devices")
    def devices(market: str | None = None):
        return catalog.list_devices(market)

    @app.post("/devices/{device_id}/retire")
    def retire(device_id: str, body: RetireIn):
        return catalog.retire(device_id, body.retired_at, body.expected_revision)

    @app.post("/ingest/{source}/preview")
    def preview(source: str, body: BatchIn):
        return intake.preview(source, body.rows)

    @app.post("/ingest/{source}")
    def ingest(source: str, body: BatchIn):
        return intake.submit(source, body.request_key, body.rows)

    @app.get("/batches/{batch_id}")
    def batch(batch_id: str):
        return intake.batch(batch_id)

    @app.get("/tickets/{ticket_id}")
    def ticket(ticket_id: str):
        return service.ticket(ticket_id)

    @app.post("/tickets/{ticket_id}/transitions")
    def transition(ticket_id: str, body: TransitionIn):
        return service.transition(ticket_id, body.action, body.at, body.expected_revision, body.note)

    @app.get("/devices/{device_id}/tickets")
    def device_tickets(device_id: str):
        return service.list_for_device(device_id)

    @app.get("/devices/{device_id}/telemetry")
    def device_telemetry(device_id: str):
        return telemetry.list_for_device(device_id)

    @app.get("/analytics/{market}")
    def live(market: str, from_day: str, to_day: str):
        return analytics.live(market, from_day, to_day)

    @app.get("/quality/{market}")
    def quality(market: str):
        return analytics.quality(market)

    @app.get("/fleet/{market}")
    def fleet(market: str, from_day: str, to_day: str):
        return analytics.fleet_summary(market, from_day, to_day)

    @app.get("/timeline/{device_id}")
    def timeline(device_id: str, from_day: str, to_day: str):
        return analytics.device_timeline(device_id, from_day, to_day)

    @app.post("/candidates")
    def prepare(body: CandidateIn):
        return publication.prepare(body.market, body.from_day, body.to_day, body.expected_head)

    @app.get("/candidates/{candidate_id}")
    def candidate(candidate_id: str):
        return publication.candidate(candidate_id)

    @app.post("/candidates/{candidate_id}/discard")
    def discard(candidate_id: str):
        return publication.discard(candidate_id)

    @app.post("/candidates/{candidate_id}/publish")
    def publish(candidate_id: str, body: PublishIn):
        return publication.publish(candidate_id, body.expected_head, body.expected_source_revision)

    @app.get("/publications/{market}")
    def versions(market: str):
        return publication.versions(market)

    @app.get("/publications/{market}/{version}")
    def release(market: str, version: int):
        return publication.read(market, version)

    @app.get("/publications/{market}/{version}/csv")
    def csv(market: str, version: int):
        return PlainTextResponse(publication.export_csv(market, version), media_type="text/csv; charset=utf-8")

    @app.get("/publications/{market}/diff/{old}/{new}")
    def diff(market: str, old: int, new: int):
        return publication.diff(market, old, new)

    @app.get("/audit")
    def audit(subject: str | None = None, limit: int = 100):
        if not 1 <= limit <= 500:
            raise DomainError("invalid_limit", "limit must be 1..500")
        return db.audit_trail(subject, limit)

    @app.post("/contracts/{source}")
    def register_contract(source: str, body: ContractIn):
        return contracts.register(source, body.effective_at, body.required, body.aliases, body.expected_latest)

    @app.get("/contracts/{source}")
    def list_contracts(source: str):
        return contracts.list(source)

    @app.get("/events/{source}/{event_key}")
    def trace_event(source: str, event_key: str):
        return contracts.trace(source, event_key)

    @app.get("/reconcile/{market}")
    def inspect(market: str, from_day: str, to_day: str):
        return reconciliation.inspect(market, from_day, to_day)

    @app.post("/reconcile/{market}")
    def save_reconciliation(market: str, from_day: str, to_day: str, body: RevisionIn):
        return reconciliation.save(market, from_day, to_day, body.expected_source_revision)

    @app.get("/reconciliations/{identifier}")
    def read_reconciliation(identifier: str):
        return reconciliation.read(identifier)

    @app.post("/alerts/{market}/evaluate")
    def evaluate_alerts(market: str, from_day: str, to_day: str, body: RevisionIn):
        return alerts.evaluate(market, from_day, to_day, body.expected_source_revision)

    @app.get("/alerts/{market}")
    def list_alerts(market: str, kind: str | None = None):
        return alerts.list(market, kind)

    @app.post("/annotations")
    def create_annotation(body: AnnotationIn):
        return annotations.create(body.market, body.device_id, body.day, body.category, body.text)

    @app.put("/annotations/{identifier}")
    def replace_annotation(identifier: str, body: AnnotationReplaceIn):
        return annotations.replace(identifier, body.text, body.expected_revision)

    @app.get("/annotations/{market}")
    def list_annotations(market: str, day: str | None = None, device_id: str | None = None):
        return annotations.list(market, day, device_id)

    @app.post("/exports")
    def create_export(body: ExportIn):
        return exports.create(body.market, body.version, body.format)

    @app.get("/exports/{identifier}")
    def read_export(identifier: str):
        return exports.read(identifier)

    @app.get("/exports/market/{market}")
    def list_exports(market: str):
        return exports.list(market)

    @app.post("/policies/{market}")
    def add_policy(market: str, body: PolicyIn):
        return policies.register(market, body.effective_at, body.response_hours, body.closure_hours, body.expected_latest)

    @app.get("/policies/{market}")
    def list_policies(market: str):
        return policies.list(market)

    @app.get("/reviews/{market}")
    def inspect_review(market: str, from_day: str, to_day: str):
        return reviews.inspect(market, from_day, to_day)

    @app.post("/reviews/{market}")
    def save_review(market: str, from_day: str, to_day: str, body: RevisionIn):
        return reviews.save(market, from_day, to_day, body.expected_source_revision)

    @app.get("/saved-reviews/{identifier}")
    def saved_review(identifier: str):
        return reviews.read(identifier)

    @app.post("/comparison")
    def compare(body: CompareIn):
        return comparison.compare(body.selections, body.product, body.from_day, body.to_day)

    @app.post("/investigations")
    def open_investigation(body: InvestigationIn):
        return investigations.open(body.alert_id, body.owner)

    @app.get("/investigations/{identifier}")
    def get_investigation(identifier: str):
        return investigations.get(identifier)

    @app.post("/investigations/{identifier}/transitions")
    def transition_investigation(identifier: str, body: InvestigationTransitionIn):
        return investigations.transition(identifier, body.action, body.expected_revision, body.owner, body.note)

    @app.get("/investigations/market/{market}")
    def list_investigations(market: str, state: str | None = None):
        return investigations.list(market, state)

    @app.get("/lineage/{market}/{version}/{day}/{product}")
    def explain_metric(market: str, version: int, day: str, product: str):
        return lineage.explain(market, version, day, product)

    @app.get("/lineage/batch/{batch_id}")
    def batch_lineage(batch_id: str):
        return lineage.batch_trace(batch_id)

    @app.post("/rollups/{market}/{version}")
    def build_rollup(market: str, version: int):
        return rollups.build(market, version)

    @app.get("/rollups/{market}/{version}")
    def read_rollup(market: str, version: int):
        return rollups.read(market, version)

    @app.post("/samples")
    def sample(body: SampleIn):
        return sampling.select(body.market, body.version, body.size, body.seed)

    @app.post("/mappings/{source}")
    def register_mapping(source: str, body: MappingIn):
        return mappings.register(source, body.external_id, body.device_id, body.valid_from, body.valid_to)

    @app.get("/mappings/{source}")
    def list_mappings(source: str, external_id: str | None = None):
        return mappings.list(source, external_id)

    @app.post("/providers")
    def register_provider(body: ProviderIn):
        return providers.register_provider(body.code, body.name)

    @app.get("/providers")
    def list_providers():
        return providers.list_providers()

    @app.post("/responsibilities")
    def add_responsibility(body: ResponsibilityIn):
        return providers.add_responsibility(body.market, body.product, body.provider, body.valid_from, body.valid_to, body.expected_latest)

    @app.get("/responsibilities/{market}")
    def list_responsibilities(market: str, product: str | None = None):
        return providers.list_responsibilities(market, product)

    @app.post("/responsibilities/{responsibility_id}/close")
    def close_responsibility(responsibility_id: str, body: CloseResponsibilityIn):
        return providers.close_responsibility(responsibility_id, body.valid_to, body.expected_revision)

    @app.post("/tickets/{ticket_id}/reassignments")
    def reassign(ticket_id: str, body: ReassignmentIn):
        return providers.reassign(ticket_id, body.event_at, body.from_provider, body.to_provider, body.reason, body.expected_revision)

    @app.post("/reassignments")
    def reassignment_batch(body: ReassignmentBatchIn):
        return providers.submit_reassignments(body.request_key, body.rows)

    @app.get("/tickets/{ticket_id}/responsibility")
    def ticket_responsibility(ticket_id: str):
        return providers.timeline(ticket_id)

    @app.get("/accountability/{market}")
    def live_accountability(market: str, from_day: str, to_day: str):
        return accountability.live(market, from_day, to_day)

    @app.get("/publications/{market}/{version}/accountability")
    def published_accountability(market: str, version: int, product: str | None = None, provider: str | None = None):
        return publication.read_accountability(market, version, product, provider)

    return app


app = create_app()
