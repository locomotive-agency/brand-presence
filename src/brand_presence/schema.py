"""Create the datasets and tables needed for the configured sources and their reports."""

from google.cloud import bigquery

from brand_presence import ref_rows
from brand_presence.bigquery import query
from brand_presence.client_ref import Snapshot
from brand_presence.errors import UserError
from brand_presence.reports.definition import load
from brand_presence.rows import RunRow, columns_of
from brand_presence.sources.ai_answers import AiAnswer, AiAnswerBrand, AiAnswersRaw, AiAnswersTask
from brand_presence.sources.mentions import (
    MentionFact,
    MentionsCursor,
    MentionsRaw,
    MentionsTask,
    MentionText,
)
from brand_presence.sources.peec import (
    PeecAnswer,
    PeecAnswerBrand,
    PeecPrompt,
    PeecPull,
    PeecRaw,
    PeecTask,
)
from brand_presence.sources.search_volume import (
    SearchVolume,
    SearchVolumeHistory,
    SearchVolumeRaw,
    SearchVolumeTask,
)

REF_MODELS = tuple(
    m for m in vars(ref_rows).values() if isinstance(m, type) and getattr(m, "table", None)
)

MODELS = (
    PeecRaw, PeecTask, PeecPrompt, PeecPull, PeecAnswer, PeecAnswerBrand,
    MentionsRaw, MentionFact, MentionText, MentionsTask, MentionsCursor,
    AiAnswer,
    AiAnswerBrand,
    AiAnswersRaw,
    AiAnswersTask,
    *REF_MODELS,
    RunRow,
    Snapshot,
    SearchVolumeRaw,
    SearchVolumeTask,
    SearchVolumeHistory,
    SearchVolume,
)


def initialise(bq) -> None:
    settings = bq.settings
    for name in settings.datasets.model_dump().values():
        dataset = bigquery.Dataset(f"{settings.project_id}.{name}")
        dataset.location = settings.location
        existing = bq.client.create_dataset(dataset, exists_ok=True)
        if existing.location.casefold() != settings.location.casefold():
            raise UserError("an existing dataset has a different location")
    for model in MODELS:
        columns = ", ".join(
            f"{name} {kind}" + (" NOT NULL" if required else "")
            for name, (kind, required) in columns_of(model).items()
        )
        partition = " PARTITION BY period_start" if model is SearchVolume else ""
        if model is MentionFact:
            partition = " PARTITION BY match_date"
        if model in (MentionsRaw, MentionText):
            column = "content_date" if model is MentionsRaw else "match_date"
            partition = (f" PARTITION BY {column} OPTIONS "
                         f"(partition_expiration_days = {settings.mention_text_days})")
        query(bq, f"CREATE TABLE IF NOT EXISTS {model.table} ({columns}){partition}")
        if model in (MentionsRaw, MentionText):
            query(bq, f"ALTER TABLE {model.table} SET OPTIONS "
                  f"(partition_expiration_days = {settings.mention_text_days})")
    for name in ("matches_unlisted", "rate_limit_waits"):
        query(bq, f"ALTER TABLE ops.runs ADD COLUMN IF NOT EXISTS {name} INT64")
    for model in REF_MODELS:
        if model is ref_rows.Prompt:
            sql = (
                "SELECT member.* FROM ref.benchmark_prompts member "
                "JOIN ref.current_benchmark_sets held USING "
                "(client_brand_id, country_code, benchmark_version)"
            )
        elif model in (ref_rows.CompetitorRow, ref_rows.Keyword):
            continue
        else:
            sql = (
                f"SELECT * FROM {model.table} WHERE load_number = "
                "(SELECT MAX(load_number) FROM ops.config_snapshots)"
            )
        query(bq, f"CREATE OR REPLACE VIEW ref.current_{model.table.split('.')[1]} AS {sql}")
    for report in load():
        columns = ", ".join(
            f"{c['name']} {c['type']}" + ("" if c["nullable"] else " NOT NULL")
            for c in report.columns
        )
        partition = (
            " PARTITION BY month" if any(c["name"] == "month" for c in report.columns) else ""
        )
        query(bq, f"CREATE TABLE IF NOT EXISTS {report.table} ({columns}){partition}")
