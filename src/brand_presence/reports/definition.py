"""Load the two production definitions in dependency order."""
from dataclasses import dataclass
from pathlib import Path

import yaml

DIRECTORY = Path(__file__).parent
NAMES = ("fct_search_volume_v1", "search_volume_history_v1")


@dataclass(frozen=True)
class Report:
    name: str
    definition: dict
    statement: str

    @property
    def fact(self):
        return self.definition.get("kind") == "fact"

    @property
    def table(self):
        return f"{'core' if self.fact else 'mart'}.{self.name}"

    @property
    def columns(self):
        return self.definition["columns"]

    @property
    def returned(self):
        return [c["name"] for c in self.columns if not self.fact or c["name"] != "pulled_at"]

    @property
    def checks(self):
        return [(name, (DIRECTORY / f"{self.name}.checks" / f"{name}.sql").read_text())
                for name in self.definition["checks"]]


def load():
    return [Report(name, yaml.safe_load((DIRECTORY / f"{name}.yaml").read_text()),
                   (DIRECTORY / f"{name}.sql").read_text()) for name in NAMES]
