import copy
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from brand_presence.settings import Settings, credentials, load

EXAMPLE = yaml.safe_load((Path(__file__).parents[1] / "example.yaml").read_text())


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
