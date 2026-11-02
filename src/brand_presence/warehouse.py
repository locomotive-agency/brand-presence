"""Bind the port's logical table names to one configured project."""
import re

from google.cloud import bigquery

from brand_presence.settings import Settings

TABLE = re.compile(r"(?<![\w.])(ops|raw|stg|core|mart)\.([A-Za-z_][A-Za-z0-9_]*)")


def qualify(sql: str, settings: Settings) -> str:
    return TABLE.sub(lambda match: f"`{settings.table(match[0])}`", sql)


class Client:
    def __init__(self, client, settings: Settings):
        self.client = client
        self.settings = settings

    def query(self, sql, job_config=None):
        return self.client.query(qualify(sql, self.settings), job_config=job_config,
                                 location=self.settings.location)


def connect(settings: Settings) -> Client:
    client = bigquery.Client(project=settings.project_id, location=settings.location)
    return Client(client, settings)
