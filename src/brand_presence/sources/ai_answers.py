"""AI answers: what AI engines answer to a client's prompts, from Cloro.

A run asks each prompt of each benchmark set of each engine the prompt lists, in each
location it lists, once per period. With no location listed it is asked for the country.
The country, the region's code and the location's name come from the client's settings.
Nothing here names a country, a region or a place.

The vendor is given each request's key as its idempotency key, so it makes one task for a
key however often the key is sent. That is also how a submission of unknown outcome is
looked for: the same key is sent again, and the vendor makes the task or reports the key as
already existing. A key it reports as existing is never counted as answered. Where its
answer names the task, the task is collected like any other. Where it does not, the attempt
stays unknown without a new paid key.

A task is an answer once the vendor reports it completed or failed, and not before. A
failed task is kept raw like any other and is judged by the rules, so what the vendor
charged for it, if anything, is counted. The rules are in ai_answers_rules.py. A task is
pending while it is queued or processing, and only then: a task in any other state, or in
none, fails as `unknown task state`.

An answer is one row in `stg.ai_answers`, with every link it cites as the vendor gives it,
each once in the answer's order, beside the domains of those links. Each brand of the row's
benchmark set is one row in `stg.ai_answer_brands`: whether the brand is named in the text,
whether one of its domains is cited, where in the text it is first named, and its rank among
the set's brands by that place. A brand is named when its name or an alias is in the text as
whole words, in any letter case. A results page with results and no AI overview is an
answer with the state `none shown`, no text, no link and no brand rows.

What a usable answer cannot give is counted: a source it cites that cannot be read, or a
link with no domain that can be read, is counted as `links_unreadable`, the link kept where
it is one; a model that is no text is null and counted as `values_unreadable`; an answer
that places no row, because no prompt
of the load it was planned under is the one it answers, is counted as `answers_unplaced`.
A request's set that the load does not hold is a fault of the load, and nothing of the
answer is written.

A task the vendor does not make because the engine asked is down on its side is not a
refusal. It is `rejected` with the class `engine unavailable`, which the source names as
unavailable: it uses no attempt, its request is pending, and the next run in the period
submits it again under a new key. Every other refusal is `task refused` and uses an attempt.

A look for a submission of unknown outcome is itself a submission here, so the framework
treats it as one (`look_submits`): it goes out a batch at a time with the ledger written
between, and never for a request of a period that has ended. An ended period
keeps its unknown rows without another call, as recorded in docs/adr/002-ai-and-mentions.md.
A refused or unavailable look records its class while leaving the row unknown, so later
runs do not repeat that submission until its outcome has been investigated and cleared.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import ClassVar
from urllib.parse import urlsplit

from pydantic import ValidationError

from brand_presence import status
from brand_presence.client_ref import BenchmarkSet, Brand, ClientRef, Location, Prompt
from brand_presence.rows import CleanRow, LedgerRow, RawRow
from brand_presence.runs import Period
from brand_presence.sources import ai_answers_rules as rules
from brand_presence.sources.base import (
    TASKS,
    Answer,
    Outgoing,
    Parsed,
    Request,
    SendContext,
    Source,
    Submitted,
    Target,
    TaskFailed,
    TaskSend,
    Verdict,
    request_of,
    set_of,
    standard_columns,
)
from brand_presence.sources.brand_names import first_named_at
from brand_presence.vendors import cloro
from brand_presence.vendors.cloro import Cloro

COUNTRY, REGION = "country", "region"
NAME = "ai-answers"

# The engine that reads the AI overview of a results page, as the YAML configuration names it.
OVERVIEW = "ai-overview"
# The vendor's task type for each engine the YAML configuration may name.
TASK_TYPES = {
    "chatgpt": "CHATGPT",
    "perplexity": "PERPLEXITY",
    "gemini": "GEMINI",
    OVERVIEW: rules.OVERVIEW_TASK,
}
ENGINES = {task_type: engine for engine, task_type in TASK_TYPES.items()}

# The states of an answer.
ANSWERED = "answered"
NONE_SHOWN = "none shown"

# What a parse counts (src/brand_presence/logs.py): a usable answer that placed no row, a value
# that cannot be read, and a cited source or link that cannot be read.
UNPLACED = "answers_unplaced"
UNREADABLE = "values_unreadable"
LINKS_UNREADABLE = "links_unreadable"

# The class of a task the vendor refused to make, and of one it did not make because the
# engine asked is down on its side.
TASK_REFUSED = status.TASK_REFUSED
ENGINE_UNAVAILABLE = status.ENGINE_UNAVAILABLE


# Row models --------------------------------------------------------------------------------

class AiAnswersRaw(RawRow):
    table: ClassVar[str] = "raw.ai_answers"


class AiAnswer(CleanRow):
    """One answer: a prompt asked of an engine in a location, in one period."""

    table: ClassVar[str] = "stg.ai_answers"
    natural_key: ClassVar[tuple[str, ...]] = (
        "client_brand_id", "country_code", "location_id", "prompt_id", "engine", "period",
    )

    prompt_id: str
    engine: str
    state: str
    # Null where no overview was shown.
    answer_text: str | None
    # The model, as the vendor names it.
    model: str | None
    # The domains of the pages the answer cites, each once.
    cited_domains: list[str]
    # The links the answer cites, as the vendor gives them, each once, in the answer's
    # order. Empty where no links were captured.
    cited_links: list[str]


class AiAnswerBrand(CleanRow):
    """One brand of the benchmark set, in one answer. Its rows follow the answer's: a newer
    answer to the same prompt, engine and location takes the place of all of them."""

    table: ClassVar[str] = "stg.ai_answer_brands"
    parent: ClassVar[type[CleanRow]] = AiAnswer
    natural_key: ClassVar[tuple[str, ...]] = (
        "client_brand_id", "country_code", "location_id", "prompt_id", "engine",
        "subject_brand_id", "period",
    )

    prompt_id: str
    engine: str
    subject_brand_id: str
    named: bool
    cited: bool
    # Where the brand is first named: the number of characters of answer_text before it.
    # Null where it is not named or its position was not captured.
    first_mention_at: int | None
    # 1 for the brand of the set named first, and so on; brands first named at the same
    # place share a rank. Null where first_mention_at is.
    mention_rank: int | None


class AiAnswersTask(LedgerRow):
    table: ClassVar[str] = "ops.ai_answers_tasks"


# Sending ------------------------------------------------------------------------------------

class CloroSend(TaskSend):
    """The vendor's side of an AI answers run."""

    batch_size = cloro.TASKS_PER_BATCH
    # Sending a key again makes the task where the vendor holds none.
    look_submits = True

    def __init__(self, context: SendContext):
        self.api = Cloro(context.http, context.keys[cloro.KEY], sleep=context.sleep)

    def _send(self, batch: Sequence[Outgoing]) -> list[tuple[Outgoing, cloro.Taken | None]]:
        """One batch, each request with what the vendor said to it."""
        taken = self.api.submit([
            (item.key, item.body["taskType"], item.body["payload"]) for item in batch
        ])
        return list(zip(batch, taken, strict=True))

    def submit(self, batch: Sequence[Outgoing]) -> list[Submitted]:
        said = []
        for item, taken in self._send(batch):
            if taken is None:
                continue
            if taken.task_id:
                # A task the vendor made, or one it holds already and names. Either way it
                # is with the vendor, and is an answer only once it is collected.
                said.append(Submitted(item.key, task_id=taken.task_id))
            elif taken.unavailable:
                said.append(Submitted(item.key, error_class=ENGINE_UNAVAILABLE))
            elif taken.refused:
                said.append(Submitted(item.key, error_class=TASK_REFUSED))
            # A key reported as existing with no task named is left out: its outcome is
            # unknown, and the next run looks for it.
        return said

    def find(self, batch: Sequence[Outgoing]) -> list[Submitted]:
        """Send the same keys again. The vendor makes the task, or names the one it holds.
        A key it reports as existing without naming the task is not found."""
        found = []
        for at in range(0, len(batch), self.batch_size):
            found.extend(self.submit(batch[at:at + self.batch_size]))
        return found

    def collect(self, task_id: str, key: str) -> Answer | None:
        answer = self.api.get(task_id)
        state = cloro.status_of(answer)
        # Queued or processing: not finished, and it stays submitted.
        if state in cloro.PENDING:
            return None
        # A state the vendor is not known to give, or none, is no reason to wait.
        if state not in cloro.FINISHED:
            raise TaskFailed(status.UNKNOWN_TASK_STATE)
        return Answer(answer)


# Settings into requests ---------------------------------------------------------------------

def places(settings: ClientRef, benchmark_set: BenchmarkSet, prompt: Prompt) -> list[Location]:
    """The locations a prompt is asked in: those it lists, or the country's own when it
    lists none. Only a country and a region can be asked for."""
    of_country = [place for place in settings.locations
                  if place.country_code == benchmark_set.country_code]
    if prompt.location_ids:
        listed = set(prompt.location_ids)
        return [place for place in of_country
                if place.location_id in listed and place.kind in (COUNTRY, REGION)]
    return [place for place in of_country if place.kind == COUNTRY][:1]


def subdivision(region_code: str, country_code: str) -> str:
    """A region's code as the vendor takes it beside the country: the part of the ISO
    3166-2 code that follows the country's own."""
    prefix = f"{country_code}-"
    return region_code[len(prefix):] if region_code.startswith(prefix) else region_code


def payload(engine: str, text: str, country_code: str, place: Location) -> dict | None:
    """What the vendor is asked for one prompt, engine and location. The overview engine
    takes a query and a location's name; the chat engines take a prompt and a region's
    code. None for a region the settings give no code for."""
    if engine == OVERVIEW:
        body: dict[str, object] = {
            "query": text, "country": country_code,
            "include": {"aioverview": {"markdown": False}},
        }
        if place.kind == REGION:
            body["location"] = place.canonical_name
        return body
    body = {"prompt": text, "country": country_code}
    if place.kind == REGION:
        if not place.region_code:
            return None
        body["state"] = subdivision(place.region_code, country_code)
    return body


def asked_text(body: Mapping[str, object]) -> str | None:
    """The prompt a request asks, whichever engine it is for."""
    held = body.get("payload")
    if not isinstance(held, dict):
        return None
    text = held.get("query") if body.get("taskType") == rules.OVERVIEW_TASK else held.get("prompt")
    return text if isinstance(text, str) else None


def brands_of(settings: ClientRef, benchmark_set: BenchmarkSet) -> list[Brand]:
    """The brands of a benchmark set: the client brand, then its competitors."""
    own = settings.brand(benchmark_set.client_brand_id)
    return [*([own] if own is not None else []), *benchmark_set.competitors]


# Brands in an answer ------------------------------------------------------------------------

def domain_of(address: str) -> str | None:
    """The domain of a cited page: lower case, with no `www.` before it. None for an
    address with no domain that can be read, which the caller counts."""
    try:
        found = urlsplit(address if "//" in address else f"//{address}").hostname
    except ValueError:
        return None
    if not found:
        return None
    found = found.lower().rstrip(".")
    return found[4:] if found.startswith("www.") else found


def ranks_of(places: Mapping[str, int | None]) -> dict[str, int | None]:
    """Each brand's rank by where it is first named: one more than the number of brands
    named before it, so brands first named at the same place share a rank. None for a
    brand that is not named."""
    named = [place for place in places.values() if place is not None]
    return {brand_id: None if place is None else 1 + sum(other < place for other in named)
            for brand_id, place in places.items()}


def is_cited(domains: Sequence[str], brand: Brand) -> bool:
    """Whether one of the cited domains is a domain of the brand, or is under one."""
    own = [domain_of(listed) for listed in brand.domains]
    return any(cited == listed or cited.endswith("." + listed)
               for cited in domains for listed in own if listed)


# The source --------------------------------------------------------------------------------

class AiAnswersSource(Source):
    name = NAME
    shape = TASKS
    raw_model = AiAnswersRaw
    clean_models = (AiAnswer, AiAnswerBrand)
    ledger_model = AiAnswersTask
    # An engine down at the vendor uses no attempt: the next tick sends the request again.
    unavailable = (ENGINE_UNAVAILABLE,)

    def plan(self, settings: ClientRef, period: Period) -> list[Request]:
        found = []
        for benchmark_set in settings.benchmark_sets:
            for prompt in benchmark_set.prompts:
                for place in places(settings, benchmark_set, prompt):
                    target = Target(benchmark_set.client_brand_id, benchmark_set.country_code,
                                    place.location_id)
                    for engine in prompt.engines:
                        if engine not in TASK_TYPES:
                            continue
                        body = payload(engine, prompt.prompt, benchmark_set.country_code, place)
                        if body is not None:
                            found.append(Request(
                                {"taskType": TASK_TYPES[engine], "payload": body}, (target,)
                            ))
        return found

    def send(self, context: SendContext) -> CloroSend:
        return CloroSend(context)

    def usable(self, answer: Answer) -> Verdict:
        # Judged by the engine that was asked, where the framework gives the request. The
        # vendor's echo of the task type is not relied on.
        asked = answer.asked.get("taskType") if answer.asked is not None else None
        overview = None if asked is None else asked == rules.OVERVIEW_TASK
        rule = rules.tripped(answer.response, overview)
        return Verdict(rule is None, rule)

    def parse(self, raw: RawRow, settings: ClientRef) -> Parsed:
        request = request_of(raw.request)
        engine = ENGINES.get(request.body.get("taskType"))
        asked = asked_text(request.body)
        answer = json.loads(raw.response)
        overview = engine == OVERVIEW
        content = rules.content_of(answer, overview)
        text = rules.text_of(content)
        if engine is None or asked is None or not (text or rules.none_shown(answer, overview)):
            # Judged usable when it was stored, and places no row now.
            return Parsed([], counts={UNPLACED: 1})
        counts: dict[str, int] = {}
        response = answer.get("response")
        model = response.get("model") if isinstance(response, dict) else None
        if model is not None and not isinstance(model, str):
            counts[UNREADABLE] = 1
            model = None
        found, unreadable = rules.links_of(content)
        links = list(dict.fromkeys(found))
        domains = [domain_of(link) for link in links]
        unreadable += domains.count(None)
        if unreadable:
            counts[LINKS_UNREADABLE] = unreadable
        cited = list(dict.fromkeys(domain for domain in domains if domain))
        rows: list[CleanRow] = []
        refused: list[ValidationError] = []
        placed = False

        def add(model_of_row: type[CleanRow], **values: object) -> None:
            try:
                rows.append(model_of_row(**values))
            except ValidationError as error:
                refused.append(error)

        for target in request.targets:
            benchmark_set = set_of(settings, target)
            standard = standard_columns(raw, settings, target)
            brands = brands_of(settings, benchmark_set)
            for prompt in benchmark_set.prompts:
                # The prompt of the set that this request asked: the same text, of this
                # engine, in this location.
                if prompt.prompt != asked or engine not in prompt.engines or not any(
                    place.location_id == target.location_id
                    for place in places(settings, benchmark_set, prompt)
                ):
                    continue
                asked_of = {"prompt_id": prompt.prompt_id, "engine": engine}
                placed = True
                add(AiAnswer, **standard, **asked_of,
                    state=ANSWERED if text else NONE_SHOWN, answer_text=text or None,
                    model=model or None, cited_domains=cited, cited_links=links)
                if not text:
                    continue
                first_at = {brand.brand_id: first_named_at(text, brand) for brand in brands}
                ranks = ranks_of(first_at)
                for brand in brands:
                    place = first_at[brand.brand_id]
                    add(AiAnswerBrand, **standard, **asked_of, subject_brand_id=brand.brand_id,
                        named=place is not None, cited=is_cited(cited, brand),
                        first_mention_at=place, mention_rank=ranks[brand.brand_id])
        if not placed:
            counts[UNPLACED] = 1
        return Parsed(rows, counts=counts, refused=refused)
