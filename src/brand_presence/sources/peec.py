"""Weekly stored Peec chats and vendor-owned prompt versions."""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import UTC, date, datetime
from typing import ClassVar

from brand_presence import http, request_keys, status
from brand_presence.client_ref import ClientRef, InconsistentRef
from brand_presence.rows import CleanRow, LedgerRow, PullRow, RawRow
from brand_presence.runs import Period, window_of
from brand_presence.sources.ai_answers import (
    ANSWERED,
    NONE_SHOWN,
    brands_of,
    domain_of,
    first_named_at,
)
from brand_presence.sources.base import (
    WINDOW,
    Answer,
    Page,
    Parsed,
    Request,
    SendContext,
    Source,
    Target,
    Verdict,
    WindowSend,
    request_of,
    set_of,
    standard_columns,
)
from brand_presence.vendors import peec

NAME = "peec"
MAX_PAGES = 1000  # Application bound, not vendor retention or a monetary cap.


class PeecPrompt(CleanRow):
    """Append-only vendor prompt versions, including observed archive transitions."""
    table: ClassVar[str] = "stg.peec_prompts"
    natural_key: ClassVar[tuple[str, ...]] = (
        "mapping", "prompt_id", "version", "observed_at", "period")
    mapping: str
    prompt_id: str
    vendor_prompt_id: str
    version: str
    prompt_text: str
    country_code: str
    topic_id: str | None
    tag_ids: list[str]
    created_at: datetime
    is_archived: bool
    observed_at: datetime


def prompt_record(mapping: str, row: dict, country: str, observed_at: datetime,
                  metadata: dict) -> PeecPrompt:
    messages, location = row.get("messages"), row.get("user_location")
    if not isinstance(location, dict) or location.get("country") != country:
        raise http.VendorError(status.PEEC_MAPPING_INVALID)
    if not isinstance(messages, list) or len(messages) != 1 or not isinstance(messages[0], dict):
        raise http.VendorError(status.PEEC_RESPONSE_UNREADABLE)
    text = peec.reference(messages[0].get("content"))
    vendor = peec.reference(row.get("id"))
    archived, tags, topic = row.get("is_archived"), row.get("tags"), row.get("topic")
    if type(archived) is not bool or not isinstance(tags, list) or any(
        not isinstance(tag, dict) for tag in tags
    ) or (topic is not None and not isinstance(topic, dict)):
        raise http.VendorError(status.PEEC_RESPONSE_UNREADABLE)
    topic_id = peec.reference(topic.get("id")) if topic is not None else None
    tag_ids = sorted(peec.reference(tag.get("id")) for tag in tags)
    try:
        value = row["created_at"]
        if not isinstance(value, str) or len(value) != 10 or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}", value
        ):
            raise ValueError
        day = date.fromisoformat(value)
        created = datetime(day.year, day.month, day.day, tzinfo=UTC)
    except (KeyError, ValueError, TypeError):
        raise http.VendorError(status.PEEC_RESPONSE_UNREADABLE) from None
    values = dict(mapping=mapping, prompt_id=request_keys.content_hash([mapping, vendor]),
        vendor_prompt_id=vendor, prompt_text=text, country_code=country, topic_id=topic_id,
        tag_ids=tag_ids, created_at=created, is_archived=archived)
    version = request_keys.content_hash({**values, "created_at": created.isoformat()})
    return PeecPrompt(**{**metadata, **values}, version=version, observed_at=observed_at,
                      pulled_at=observed_at)


class PeecRaw(RawRow):
    table: ClassVar[str] = "raw.peec"


class PeecTask(LedgerRow):
    table: ClassVar[str] = "ops.peec_tasks"


class PeecPull(PullRow):
    table: ClassVar[str] = "stg.peec_pulls"
    natural_key: ClassVar[tuple[str, ...]] = ("mapping", "prompt_id", "engine", "period")
    mapping: str
    prompt_id: str
    engine: str
    complete_snapshot: bool
    chats_observed: int


class PeecAnswer(CleanRow):
    table: ClassVar[str] = "stg.peec_answers"
    natural_key: ClassVar[tuple[str, ...]] = ("mapping", "answer_key", "period")
    mapping: str
    answer_key: str
    prompt_id: str
    engine: str
    observation_date: date
    prompt_version: str
    answer_text: str
    state: str
    model: str | None
    citation_known: bool
    cited_links: list[str]
    cited_domains: list[str]


class PeecAnswerBrand(CleanRow):
    table: ClassVar[str] = "stg.peec_answer_brands"
    natural_key: ClassVar[tuple[str, ...]] = (
        "mapping", "answer_key", "subject_brand_id", "period")
    mapping: str
    answer_key: str
    subject_brand_id: str
    named: bool
    cited: bool | None
    first_mention_at: int | None
    mention_rank: int | None
    vendor_mentioned: bool | None
    vendor_position: int | None


def chat_id(row: dict) -> str:
    return peec.reference(row.get("id"))


def nested(row: dict, field: str) -> str | None:
    value = row.get(field)
    return value.get("id") if isinstance(value, dict) else None


def text_of(row: dict, engine: str) -> str | None:
    messages = row.get("messages")
    if not isinstance(messages, list) or any(not isinstance(m, dict) for m in messages):
        raise http.VendorError(status.PEEC_ANSWER_STATE_UNKNOWN)
    answers = [m.get("content") for m in messages if m.get("role") == "assistant"]
    if len(answers) != 1 or not isinstance(answers[0], str):
        raise http.VendorError(status.PEEC_ANSWER_STATE_UNKNOWN)
    if not answers[0].strip():
        # Verified 29 October 2026: this exact empty shape means no overview.
        if engine == "ai-overview" and all(
            row.get(field) == [] for field in ("sources", "links", "brands_mentioned")
        ):
            return ""
        if engine in ("chatgpt", "gemini", "perplexity") and answers[0] == "" and all(
            row.get(field) == [] for field in ("sources", "links", "brands_mentioned")
        ):
            return None  # A refused chat, never an answer or an absence observation.
        raise http.VendorError(status.PEEC_ANSWER_STATE_UNKNOWN)
    return answers[0]


class PeecSend(WindowSend):
    parts = ("chats", "contents")
    max_pages = MAX_PAGES

    def __init__(self, context: SendContext):
        self.api = peec.Peec(context.http, context.keys[peec.KEY], sleep=context.sleep,
                             clock=context.clock, deadline=context.deadline)
        self.ref, self.bq = context.ref, context.bq
        self.state_path = context.settings.state_path
        self.listing: list[dict] = []
        self.total: int | None = None
        self.listing_invalid = False
        self._prompts: dict[str, list[PeecPrompt]] = {}
        self.clock = context.clock
        self.run_id = context.run_id
        if self.run_id is None:
            raise ValueError("Peec discovery requires the framework run identity")
        self._counts: Counter[str] = Counter()

    def counts(self):
        found = self._counts + self.api.counts
        self._counts.clear()
        self.api.counts.clear()
        return found

    def _prompt_metadata(self, request):
        target, = request.targets
        benchmark = set_of(self.ref, target)
        return dict(client_brand_id=target.client_brand_id, location_id=target.location_id,
            benchmark_version=benchmark.version, snapshot_id=self.ref.snapshot_id,
            period=request.body["period"],
            period_start=date.fromisoformat(request.body["start_date"]), run_id=self.run_id,
            request_key=request_keys.content_hash(["peec-prompts", request.body["mapping"]]))

    def _save_discovery(self, request, rows):
        from brand_presence.bigquery import Warehouse
        from brand_presence.sources.base import stored_request

        raw = PeecRaw(
            run_id=self.run_id,
            request_key=self._prompt_metadata(request)["request_key"],
            period=request.body["period"], content_date=None, pulled_at=self.clock(),
            request=stored_request(request, self.ref.load_number, self.ref.snapshot_id),
            response=json.dumps({"prompts": rows}, ensure_ascii=False), usable=True,
            load_number=self.ref.load_number, snapshot_id=self.ref.snapshot_id,
        )
        warehouse = Warehouse(self.bq, self.state_path)
        path = warehouse.save_raw(NAME, raw, raw.request_key + ".json", self.run_id)
        self._counts["raw_rows"] += warehouse.load_raw(PeecRaw, [path])

    def _save_prompts(self, rows):
        from brand_presence.bigquery import Transaction, rows_parameter

        if not rows:
            return
        with Transaction(self.bq) as transaction:
            columns = ", ".join(PeecPrompt.model_fields)
            transaction.run(f"INSERT INTO {PeecPrompt.table} ({columns}) "
                f"SELECT {columns} FROM UNNEST(@rows)",
                [rows_parameter("rows", PeecPrompt, rows)])
        self._counts["clean_rows"] += len(rows)

    def expand(self, planned):
        found = []
        # Cache only within this run: every run re-lists all prompts, including archives.
        # Validate the entire list before saving any version or returning any request.
        discovered = {}
        for request in planned:
            body = request.body
            mapping = body["mapping"]
            if mapping not in self._prompts and mapping not in discovered:
                rows = self.api.prompts(body["project_id"])
                self._save_discovery(request, rows)
                discovered[mapping] = [prompt_record(mapping, row, body["country_code"],
                    self.clock(), self._prompt_metadata(request)) for row in rows]
        self._save_prompts([row for rows in discovered.values() for row in rows])
        self._prompts.update(discovered)
        for request in planned:
            for prompt in self._prompts[request.body["mapping"]]:
                if not prompt.is_archived:
                    found.append(Request({**request.body, "prompt_id": prompt.prompt_id,
                        "vendor_prompt_id": prompt.vendor_prompt_id,
                        "prompt_version": prompt.version}, request.targets))
        return found

    def check(self, group, requests):
        projects = [p for p in self.ref.peec_projects if p.mapping == group]
        if len(projects) != 1:
            return status.PEEC_MAPPING_INVALID
        project = projects[0]
        visible = self.api.projects()
        selected = [p for p in visible if p.get("id") == project.project_id]
        if len(selected) != 1:
            return status.PEEC_PROJECT_ACCESS
        if selected[0].get("country_code") != project.country_code:
            return status.PEEC_MAPPING_INVALID
        vendor_brands = self.api.brands(project.project_id)
        mapped = [b for b in self.ref.peec_brands if b.mapping == group]
        for brand in mapped:
            matches = [b for b in vendor_brands if b.get("id") == brand.vendor_brand_id]
            if len(matches) != 1 or matches[0].get("is_own") is not (
                brand.brand_id == project.client_brand_id
            ):
                return status.PEEC_MAPPING_INVALID
        channels = self.api.channels(project.project_id)
        for request in requests:
            channel = peec.CHANNELS.get(request.body["engine"])
            matches = [c for c in channels if c.get("id") == channel]
            if channel is None or len(matches) != 1 or matches[0].get("is_active") is not True:
                return status.PEEC_ENGINE_UNMAPPED
        return None

    def skip_part(self, request, part):
        return part == "contents" and (not self.listing or self.listing_invalid)

    def pull(self, request, part, page):
        body = request.body
        project = body["project_id"]
        if part == "chats":
            if page == 1:
                self.listing, self.total = [], None
                self.listing_invalid = False
            response = self.api.chats(project, body["vendor_prompt_id"],
                peec.CHANNELS[body["engine"]], date.fromisoformat(body["start_date"]),
                date.fromisoformat(body["end_date"]), (page - 1) * peec.PAGE_SIZE)
            try:
                batch = peec.envelope(response)
            except http.VendorError:
                # Preserve the successful HTTP response before judging its envelope.
                self.listing_invalid = True
                return Page(Answer(response), None, False)
            # Save every successful response first; complete() judges coverage and identity.
            self.listing.extend(batch)
            self.total = response["total_count"]
            more = bool(batch) and len(self.listing) < self.total
        else:
            response = self.api.content(project, chat_id(self.listing[page - 1]))
            more = page < len(self.listing)
        counts = {}
        if more and page == self.max_pages:
            counts["peec_pulls_capped"] = 1
        return Page(Answer(response, counts=counts), None, more)


class PeecSource(Source):
    name, shape = NAME, WINDOW
    raw_model, ledger_model = PeecRaw, PeecTask
    clean_models = (PeecPull, PeecAnswer, PeecAnswerBrand)
    identity_fields = ("mapping", "prompt_id", "engine")
    refusals = (status.KEY_REFUSED, status.LIMIT, status.PEEC_PROJECT_ACCESS,
                status.PEEC_MAPPING_INVALID, status.PEEC_HISTORY_UNAVAILABLE)
    ends_run = (status.KEY_REFUSED, status.LIMIT, status.PEEC_MAPPING_INVALID)
    checked_on_rebuild = False
    require_latest_attempt = True
    dynamic_requests = True

    def plan(self, settings: ClientRef, period: Period) -> list[Request]:
        start, end = window_of(period)
        found = []
        for project in settings.peec_projects:
            if end < project.first_date:
                continue
            places = [p for p in settings.locations
                      if p.country_code == project.country_code
                      and NAME in p.sources and p.kind == "country"]
            if len(places) != 1:
                raise InconsistentRef(status.PEEC_MAPPING_INVALID)
            for engine in project.engines:
                found.append(Request({
                    "mapping": project.mapping, "project_id": project.project_id,
                    "engine": engine, "country_code": project.country_code, "period": period.name,
                    "first_date": project.first_date.isoformat(),
                    "start_date": start.isoformat(), "end_date": end.isoformat(),
                }, (Target(project.client_brand_id, project.country_code,
                           places[0].location_id),)))
        return found

    def group(self, request):
        return request.body["mapping"]

    def window(self, request):
        return (date.fromisoformat(request.body["start_date"]),
                date.fromisoformat(request.body["end_date"]))

    def send(self, context):
        return PeecSend(context)

    def usable(self, answer):
        return Verdict(isinstance(answer.response, dict), status.PEEC_RESPONSE_UNREADABLE)

    def complete(self, request, answer):
        try:
            self._checked(request, answer.response)
            return Verdict(True)
        except http.VendorError as error:
            counts = {}
            if error.error_class == status.PEEC_ANSWER_DATE_INVALID:
                counts["peec_chats_undated"] = 1
            elif error.error_class in (status.PEEC_ANSWER_MISMATCH, status.PEEC_ENGINE_UNMAPPED):
                counts["answers_unplaced"] = 1
            elif error.error_class == status.PEEC_PAGINATION_INCOMPLETE:
                parts = answer.response.get("parts", {})
                rows = [row for page in parts.get("chats", []) for row in page.get("data", [])]
                ids = [row.get("id") for row in rows if isinstance(row, dict)]
                duplicates = sum(n - 1 for n in Counter(ids).values() if n > 1)
                if duplicates:
                    counts["peec_chats_duplicate"] = duplicates
                else:
                    counts["peec_chats_missing"] = max(
                        1, len(rows) - len(parts.get("contents", [])))
            return Verdict(False, error.error_class, counts)

    def _checked(self, request, response):
        if not isinstance(response, dict):
            raise http.VendorError(status.PEEC_RESPONSE_UNREADABLE)
        parts = response.get("parts")
        if not isinstance(parts, dict) or set(parts) != {"chats", "contents"}:
            raise http.VendorError(status.PEEC_RESPONSE_UNREADABLE)
        listings, contents = parts["chats"], parts["contents"]
        if not isinstance(listings, list) or not isinstance(contents, list) or any(
            not isinstance(p, dict) for p in (*listings, *contents)
        ):
            raise http.VendorError(status.PEEC_RESPONSE_UNREADABLE)
        if not listings:
            raise http.VendorError(status.PEEC_PAGINATION_INCOMPLETE)
        total = listings[0].get("total_count")
        rows = []
        for page in listings:
            rows.extend(peec.envelope(page))
            if page["total_count"] != total:
                raise http.VendorError(status.PEEC_PAGINATION_INCOMPLETE)
        if total > len(rows) and len(listings) == MAX_PAGES or total > MAX_PAGES:
            raise http.VendorError(status.PEEC_PAGE_CAP)
        if len(rows) != total or len({chat_id(r) for r in rows}) != total:
            raise http.VendorError(status.PEEC_PAGINATION_INCOMPLETE)
        if not rows:
            if contents:
                raise http.VendorError(status.PEEC_PAGINATION_INCOMPLETE)
            return []
        if len(contents) != total or {chat_id(r) for r in contents} != {chat_id(r) for r in rows}:
            raise http.VendorError(status.PEEC_PAGINATION_INCOMPLETE)
        start, end = self.window(request)
        details = {chat_id(r): r for r in contents}
        checked = []
        for row in rows:
            detail = details[chat_id(row)]
            if nested(row, "model_channel") != peec.CHANNELS.get(request.body["engine"]):
                raise http.VendorError(status.PEEC_ENGINE_UNMAPPED)
            if nested(row, "prompt") != request.body.get("vendor_prompt_id") or any(
                nested(detail, k) != nested(row, k) for k in ("prompt", "model_channel")
            ):
                raise http.VendorError(status.PEEC_ANSWER_MISMATCH)
            try:
                day = date.fromisoformat(row["date"])
                if row["date"] != day.isoformat() or not start <= day <= end:
                    raise ValueError
            except (KeyError, ValueError, TypeError):
                raise http.VendorError(status.PEEC_ANSWER_DATE_INVALID) from None
            text = text_of(detail, request.body["engine"])
            if day >= date.fromisoformat(request.body["first_date"]):
                checked.append((row, detail, day, text))
        return checked

    def parse(self, raw, settings):
        request = request_of(raw.request)
        response = json.loads(raw.response)
        checked = self._checked(request, response)
        target, = request.targets
        standard = standard_columns(raw, settings, target)
        benchmark = set_of(settings, target)
        brands = brands_of(settings, benchmark)
        mapped = {p.vendor_brand_id: p.brand_id for p in settings.peec_brands
                  if p.mapping == request.body["mapping"]}
        if set(mapped.values()) != {b.brand_id for b in brands}:
            raise InconsistentRef(status.PEEC_MAPPING_INVALID)
        rows, counts = [], Counter()
        for listing, detail, day, text in checked:
            if text is None:
                counts["peec_chats_no_text"] += 1
                continue
            links, known = [], True
            sources = detail.get("sources")
            if not isinstance(sources, list):
                sources, known = [], False
                counts["peec_citations_unknown"] += 1
            for item in sources:
                if not isinstance(item, dict) or not isinstance(item.get("url"), str) or not (
                    domain_of(item["url"]) if isinstance(item, dict) else None
                ):
                    counts["links_unreadable"] += 1
                    known = False
                    continue
                count = item.get("citation_count")
                if type(count) is not int or count < 0:
                    counts["peec_citations_unknown"] += 1
                    known = False
                elif count > 0 and item["url"] not in links:
                    links.append(item["url"])
            domains = list(dict.fromkeys(domain_of(link) for link in links))
            answer_key = request_keys.content_hash([request.body["mapping"], chat_id(listing)])
            common = {**standard, "mapping": request.body["mapping"], "answer_key": answer_key}
            model = nested(detail, "model")
            if model is not None and not isinstance(model, str):
                model = None
                counts["values_unreadable"] += 1
            rows.append(PeecAnswer(**common, prompt_id=request.body["prompt_id"],
                engine=request.body["engine"], observation_date=day, answer_text=text,
                prompt_version=request.body["prompt_version"],
                state=ANSWERED if text else NONE_SHOWN,
                model=model, citation_known=known, cited_links=links,
                cited_domains=domains))
            if not text:
                counts["peec_no_overview"] += 1
                continue
            mentions = detail.get("brands_mentioned")
            vendor = {}
            if mentions is not None and not isinstance(mentions, list):
                counts["values_unreadable"] += 1
            duplicated = set()
            if isinstance(mentions, list):
                for mention in mentions:
                    if not isinstance(mention, dict) or mention.get("id") not in mapped:
                        counts["peec_brands_unmapped"] += 1
                        continue
                    position = mention.get("position")
                    if position is not None and (type(position) is not int or position < 1):
                        counts["values_unreadable"] += 1
                        position = None
                    member = mapped[mention["id"]]
                    if member in vendor:
                        counts["values_unreadable"] += 1
                        duplicated.add(member)
                    vendor[member] = None if member in duplicated else position
            first = {b.brand_id: first_named_at(text, b) for b in brands}
            for brand in brands:
                position = first[brand.brand_id]
                mentioned = brand.brand_id in vendor if isinstance(mentions, list) else None
                if mentioned is not None and mentioned != (position is not None):
                    counts["peec_mentions_differ"] += 1
                cited = any(d == own or d.endswith("." + own)
                            for d in domains for own in brand.domains) if known else None
                rows.append(PeecAnswerBrand(**common, subject_brand_id=brand.brand_id,
                    named=position is not None, cited=cited, first_mention_at=position,
                    mention_rank=1 + sum(p < position for p in first.values() if p is not None)
                        if position is not None else None,
                    vendor_mentioned=mentioned, vendor_position=vendor.get(brand.brand_id)))
        rows.append(PeecPull(**standard, mapping=request.body["mapping"],
            prompt_id=request.body["prompt_id"], engine=request.body["engine"],
            window_start=self.window(request)[0], window_end=self.window(request)[1],
            rows_written=len(rows), pages=sum(len(p) for p in response["parts"].values()),
            row_limit_hit=False, source_time_zone=None, complete_snapshot=True,
            chats_observed=len(checked)))
        return Parsed(rows, counts)
