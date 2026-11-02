from pathlib import Path

import yaml

from brand_presence.rows import columns_of
from brand_presence.sources.search_volume import SearchVolume
from brand_presence.warehouse import qualify
from project import ROOT


def test_clean_model_matches_the_ported_contract():
    contract = yaml.safe_load((ROOT / "src/brand_presence/contracts/clean/stg.search_volume.yaml")
                              .read_text())
    assert columns_of(SearchVolume) == {
        c["name"]: (c["type"], not c["nullable"]) for c in contract["columns"]}
    assert tuple(contract["key"]) == SearchVolume.natural_key


def test_each_logical_table_is_bound_to_the_configured_project():
    from brand_presence.settings import load

    settings = load(ROOT / "example.yaml")
    settings.datasets.stg = "example_clean"
    assert qualify("SELECT * FROM stg.search_volume", settings) == (
        "SELECT * FROM `example-project.example_clean.search_volume`")


def test_package_has_no_private_identifiers_or_services():
    import re

    # Split the forbidden marker so this test does not publish it itself.
    private = "locomotive" + "-bp-"
    files = [*Path("src").rglob("*"), Path("example.yaml"), Path("pyproject.toml")]
    for path in files:
        if path.is_file() and path.suffix in (".py", ".yaml", ".sql", ".toml"):
            text = path.read_text()
            assert private not in text.lower()
            assert not re.search(r"[\w.+-]+@[\w.-]+\.[a-z]{2,}", text)
            assert not re.search(r"google.cloud.(storage|secretmanager)|opentelemetry", text)
