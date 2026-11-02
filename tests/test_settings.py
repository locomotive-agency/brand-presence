import copy

import pytest
import yaml
from pydantic import ValidationError

from brand_presence.settings import Settings, credentials, load
from project import configuration

EXAMPLE = configuration()


@pytest.mark.parametrize("field,value", [
    ("project_id", "example` ; DROP TABLE anything"),
    ("project_id", "../example"),
    ("time_zone", "invalid/timezone"),
    ("brands", []),
    ("countries", []),
    ("benchmark_sets", []),
    ("password", "example-secret"),
    ("datasets", {"stg": "unsafe.table"}),
    ("datasets", {"raw": "bp_ops"}),
])
def test_invalid_configuration_is_refused(field, value):
    config = copy.deepcopy(EXAMPLE)
    config[field] = value
    with pytest.raises(ValidationError):
        Settings.model_validate(config)


@pytest.mark.parametrize("change", ["brand", "country", "duplicate", "generic", "term_type"])
def test_invalid_references_are_refused(change):
    config = copy.deepcopy(EXAMPLE)
    group = config["benchmark_sets"][0]
    if change == "brand":
        group["client_brand_id"] = "unlisted"
    elif change == "country":
        group["country_code"] = "XX"
    elif change == "duplicate":
        group["keywords"].append(group["keywords"][0])
    elif change == "generic":
        group["keywords"][1]["brand_id"] = "aardvark"
    else:
        group["keywords"][0]["term_type"] = "competitor"
    with pytest.raises(ValidationError):
        Settings.model_validate(config)


def test_state_directory_is_relative_to_the_configuration(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(EXAMPLE))
    assert load(path).state_dir == tmp_path / ".brand-presence"


def test_credentials_are_read_from_environment_and_never_written(monkeypatch):
    monkeypatch.delenv("DATAFORSEO_LOGIN", raising=False)
    monkeypatch.delenv("DATAFORSEO_PASSWORD", raising=False)
    with pytest.raises(ValueError, match="DATAFORSEO_LOGIN"):
        credentials()
    monkeypatch.setenv("DATAFORSEO_LOGIN", "example-login")
    monkeypatch.setenv("DATAFORSEO_PASSWORD", "example-password")
    assert credentials() == {"dataforseo-login": "example-login",
                              "dataforseo-password": "example-password"}


@pytest.mark.parametrize("client_id", [None, "example", " example "])
def test_client_id_must_be_explicit_and_not_the_example(client_id):
    config = copy.deepcopy(EXAMPLE)
    if client_id is None:
        config.pop("client_id", None)
    else:
        config["client_id"] = client_id
    with pytest.raises(ValidationError, match="client_id"):
        Settings.model_validate(config)


def test_example_client_id_error_explains_what_to_change():
    config = copy.deepcopy(EXAMPLE)
    config["client_id"] = "example"
    with pytest.raises(ValidationError, match="set client_id to your own stable identifier"):
        Settings.model_validate(config)


@pytest.mark.parametrize("language", ["nl", "en", "fil", "zh-TW", "zh-CN"])
def test_language_code_is_preserved_in_vendor_requests(tmp_path, respx_mock, language):
    from project import Project

    config = copy.deepcopy(EXAMPLE)
    config["countries"][0]["language"] = language
    settings = Settings.model_validate(config)
    project = Project(tmp_path, respx_mock)
    project.settings.countries = settings.countries
    project.run()
    assert len(project.vendor.tasks) == 2
    assert {body["language_code"] for body in project.vendor.tasks.values()} == {language}


@pytest.mark.parametrize("language", ["english", "zh_tw", "zh-tw", "zh-", "zh-TWW", "en/US"])
def test_malformed_language_codes_are_refused(language):
    config = copy.deepcopy(EXAMPLE)
    config["countries"][0]["language"] = language
    with pytest.raises(ValidationError, match="language"):
        Settings.model_validate(config)
