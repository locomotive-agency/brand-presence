"""Load the ported production definitions in dependency order."""

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
        return [
            (name, (DIRECTORY / f"{self.name}.checks" / f"{name}.sql").read_text())
            for name in self.definition["checks"]
        ]


GROUPS = {

    "search-volume": NAMES,
    "ai-answers": (
        "fct_ai_answers_v1",
        "fct_ai_cited_links_v1",
        "ai_prompt_results_v2",
        "ai_keyword_monthly_v2",
        "ai_citations_v1",
    ),
}

GROUPS["mentions"] = ("fct_mentions_v1",)


GROUPS["peec"] = ("fct_peec_answers_v1", "fct_peec_cited_links_v1", "peec_prompt_results_v1",
                  "peec_keyword_monthly_v1", "peec_citations_v1")


WEEKLY = ("ai_brand_mentions_weekly_v1", "ai_domain_citations_weekly_v1",
          "ai_overview_presence_weekly_v1")
GROUPS["ai-weekly"] = GROUPS["ai-answers"] + GROUPS["peec"] + WEEKLY


def load(source=None):
    return [
        Report(
            name,
            yaml.safe_load((DIRECTORY / f"{name}.yaml").read_text()),
            (DIRECTORY / f"{name}.sql").read_text(),
        )
        for name in (GROUPS[source] if source else tuple(dict.fromkeys(sum(GROUPS.values(), ()))))
    ]
