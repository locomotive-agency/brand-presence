"""Create the datasets and tables needed for search volume and its reports."""
from google.cloud import bigquery

from brand_presence.bigquery import query
from brand_presence.client_ref import Snapshot
from brand_presence.errors import UserError
from brand_presence.reports.definition import load
from brand_presence.rows import RunRow, columns_of
from brand_presence.sources.search_volume import (
    SearchVolume,
    SearchVolumeHistory,
    SearchVolumeRaw,
    SearchVolumeTask,
)

MODELS = (RunRow, Snapshot, SearchVolumeRaw, SearchVolumeTask, SearchVolumeHistory, SearchVolume)


def initialise(bq) -> None:
    settings = bq.settings
    for name in settings.datasets.model_dump().values():
        dataset = bigquery.Dataset(f"{settings.project_id}.{name}")
        dataset.location = settings.location
        existing = bq.client.create_dataset(dataset, exists_ok=True)
        if existing.location.casefold() != settings.location.casefold():
            raise UserError("an existing dataset has a different location")
    for model in MODELS:
        columns = ", ".join(f"{name} {kind}" + (" NOT NULL" if required else "")
                            for name, (kind, required) in columns_of(model).items())
        partition = " PARTITION BY period_start" if model is SearchVolume else ""
        query(bq, f"CREATE TABLE IF NOT EXISTS {model.table} ({columns}){partition}")
    for report in load():
        columns = ", ".join(f"{c['name']} {c['type']}" +
                            ("" if c["nullable"] else " NOT NULL") for c in report.columns)
        query(bq, f"CREATE TABLE IF NOT EXISTS {report.table} ({columns}) PARTITION BY month")
