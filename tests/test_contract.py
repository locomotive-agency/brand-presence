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
        "canonical-" + "google-sources", "brand-presence-" + "reporting",
        "add-" + "google-sources", "secret" + " manager", "cloud" + " run",
        "terra" + "form", "source-" + "framework", "data-" + 'model "',
    )
    tracked = [
        str(p.relative_to(ROOT))
        for root in ("src", "tests", "docs")
        for p in (ROOT / root).rglob("*")
        if p.is_file()
        and "__pycache__" not in p.parts
        and p.suffix in (".py", ".sql", ".yaml", ".json", ".md", ".csv", ".html")
    ]
    tracked += ["README.md", "example.yaml", "pyproject.toml", "CONTEXT.md",
                "CONTRIBUTING.md", "SECURITY.md"]
    assert any(name.startswith("src/") for name in tracked)
    assert "src/brand_presence/example/inputs.json" in tracked
    assert "src/brand_presence/example/expected/fct_search_volume_v1.csv" in tracked
    for name in filter(None, tracked):
        path = ROOT / name
        if path.is_file():
            text = path.read_text()
            assert not any(marker in text.casefold() for marker in private), name
            assert not re.search(r"\(D[0-9]+\)", text), name
            assert not re.search(r"[\w.+-]+@[\w.-]+\.[a-z]{2,}", text), name
            assert not re.search(
                r"google.cloud.(storage|secretmanager)|open" + "telemetry", text
            ), name


def test_example_keeps_optional_sources_disabled():
    config = yaml.safe_load((ROOT / 'example.yaml').read_text())
    assert not config.get('mention_filters')
    assert not config.get('subreddits')
    assert not config.get('peec_projects')
    assert not config.get('web_properties')
    assert not config.get('ad_accounts')
    assert not config.get('modifiers')
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


def test_google_report_groups_have_all_dependencies():
    import sqlglot
    from sqlglot import exp

    from brand_presence.reports.definition import load
    from brand_presence.schema import MODELS

    available = {model.table for model in MODELS}
    available |= {name.replace('ref.', 'ref.current_') for name in available
                  if name.startswith('ref.')}
    for source in ('search-console', 'ga4', 'google-ads'):
        held = set(available)
        for report in load(source):
            tree = sqlglot.parse_one(report.statement, read='bigquery')
            reads = {f'{t.db}.{t.name}' for t in tree.find_all(exp.Table) if t.db}
            assert reads <= held, (report.name, reads - held)
            held.add(report.table)
