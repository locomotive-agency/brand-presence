import yaml

from brand_presence.rows import columns_of
from brand_presence.sources.search_volume import SearchVolume
from brand_presence.warehouse import qualify
from project import ROOT, configuration


def test_clean_model_matches_the_ported_contract():
    contract = yaml.safe_load(
        (ROOT / "src/brand_presence/contracts/clean/stg.search_volume.yaml").read_text()
    )
    assert columns_of(SearchVolume) == {
        c["name"]: (c["type"], not c["nullable"]) for c in contract["columns"]
    }
    assert tuple(contract["key"]) == SearchVolume.natural_key


def test_each_logical_table_is_bound_to_the_configured_project():
    from brand_presence.settings import Settings

    settings = Settings.model_validate(configuration())
    settings.datasets.stg = "example_clean"
    assert qualify("SELECT * FROM stg.search_volume", settings) == (
        "SELECT * FROM `example-project.example_clean.search_volume`"
    )


def test_package_has_no_private_identifiers_or_services():
    import re

    # Split the forbidden markers so this test does not publish them itself.
    private = (
        "loco" + "motive", "src/" + "pipeline", "open" + "spec", "run" + "books/",
        "design" + ".md", "canonical-" + "vendor-sources", "add-" + "run-monitoring",
        "add-" + "setup-template-6", "add-" + "city-geography", "add-" + "peec",
        "extend-" + "google-ads-v2", "ai-answer-" + "links-and-mentions",
        "resume-" + "mention-pulls",
        "canonical-" + "report-sql",
        "add-" + "serving-tables",
    )
    tracked = [
        str(p.relative_to(ROOT))
        for root in ("src", "tests", "docs")
        for p in (ROOT / root).rglob("*")
        if p.is_file()
        and "__pycache__" not in p.parts
        and p.suffix in (".py", ".sql", ".yaml", ".json", ".md")
    ]
    tracked += ["README.md", "example.yaml", "pyproject.toml", "CONTEXT.md"]
    assert any(name.startswith("src/") for name in tracked)
    for name in filter(None, tracked):
        path = ROOT / name
        if path.is_file():
            text = path.read_text()
            assert not any(marker in text.casefold() for marker in private), name
            assert not re.search(r"[\w.+-]+@[\w.-]+\.[a-z]{2,}", text), name
            assert not re.search(
                r"google.cloud.(storage|secretmanager)|open" + "telemetry", text
            ), name


def test_example_keeps_optional_sources_disabled():
    config = yaml.safe_load((ROOT / 'example.yaml').read_text())
    assert not config.get('mention_filters')
    assert not config.get('subreddits')
    assert not config.get('peec_projects')
    assert all(not group.get('prompts') for group in config['benchmark_sets'])


def test_all_ported_clean_and_ref_models_match_contracts():
    from brand_presence.schema import MODELS

    directory = ROOT / "src/brand_presence/contracts/clean"
    models = {model.table: model for model in MODELS}
    for path in directory.glob("*.yaml"):
        contract = yaml.safe_load(path.read_text())
        model = models[path.stem]
        assert columns_of(model) == {
            c["name"]: (c["type"], not c["nullable"]) for c in contract["columns"]
        }, path.name
        if hasattr(model, "natural_key"):
            assert tuple(contract["key"]) == model.natural_key, path.name
