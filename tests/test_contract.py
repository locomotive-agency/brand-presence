import subprocess

import yaml

from brand_presence.rows import columns_of
from brand_presence.sources.search_volume import SearchVolume
from brand_presence.warehouse import qualify
from project import ROOT, configuration


def test_clean_model_matches_the_ported_contract():
    contract = yaml.safe_load((ROOT / "src/brand_presence/contracts/clean/stg.search_volume.yaml")
                              .read_text())
    assert columns_of(SearchVolume) == {
        c["name"]: (c["type"], not c["nullable"]) for c in contract["columns"]}
    assert tuple(contract["key"]) == SearchVolume.natural_key


def test_each_logical_table_is_bound_to_the_configured_project():
    from brand_presence.settings import Settings

    settings = Settings.model_validate(configuration())
    settings.datasets.stg = "example_clean"
    assert qualify("SELECT * FROM stg.search_volume", settings) == (
        "SELECT * FROM `example-project.example_clean.search_volume`")


def test_package_has_no_private_identifiers_or_services():
    import re

    # Split the forbidden markers so this test does not publish them itself.
    private = ("loco" + "motive", "src/" + "pipeline")
    tracked = subprocess.check_output(
        ["git", "ls-files", "-z", "--", "src", "tests", "README.md", "docs",
         "example.yaml", "pyproject.toml"], cwd=ROOT, text=True,
    ).split("\0")
    assert any(name.startswith("src/") for name in tracked)
    for name in filter(None, tracked):
        path = ROOT / name
        if path.is_file():
            text = path.read_text()
            assert not any(marker in text.casefold() for marker in private), name
            assert not re.search(r"[\w.+-]+@[\w.-]+\.[a-z]{2,}", text), name
            assert not re.search(r"google.cloud.(storage|secretmanager)|open" + "telemetry",
                                 text), name
