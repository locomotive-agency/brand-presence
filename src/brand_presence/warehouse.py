"""Bind the port's logical table names to one configured project."""
import re

from google.cloud import bigquery

from brand_presence.settings import Settings

TABLE = re.compile(
    r"--[^\n]*|/\*.*?\*/|`[^`]*`|'(?:[^'\\]|\\.|'')*'|"
    r"(?<![\w.])(?P<logical>(?:ops|raw|stg|core|mart|ref)\.[A-Za-z_][A-Za-z0-9_]*)",
    re.DOTALL,
)


def qualify(sql: str, settings: Settings) -> str:
    return TABLE.sub(lambda match: f"`{settings.table(match['logical'])}`"
                     if match['logical'] else match[0], sql)


class Client:
    def __init__(self, client, settings: Settings):
        self.client = client
        self.settings = settings

    def query(self, sql, job_config=None, *, location=None):
        return self.client.query(qualify(sql, self.settings), job_config=job_config,
                                 location=location or self.settings.location)

    def get_dataset(self, dataset):
        return self.client.get_dataset(dataset)

    def list_tables(self, dataset):
        return self.client.list_tables(dataset)

    def get_table(self, table):
        return self.client.get_table(table)


def connect(settings: Settings) -> Client:
    client = bigquery.Client(project=settings.project_id, location=settings.location)
    return Client(client, settings)
