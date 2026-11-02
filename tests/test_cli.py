import pytest
import yaml
from typer.testing import CliRunner

from brand_presence import cli
from brand_presence.settings import load
from brand_presence.warehouse import Client
from project import ROOT, BigQuery, Vendor, configuration
from reporting import install


def test_init_collect_and_report_from_the_cli(tmp_path, respx_mock, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(configuration()))
    settings = load(path)
    fake = BigQuery(project=settings.project_id)
    monkeypatch.setattr(cli, "connect", lambda configured: Client(fake, configured))
    monkeypatch.setenv("DATAFORSEO_LOGIN", "example-login")
    monkeypatch.setenv("DATAFORSEO_PASSWORD", "example-password")
    vendor = Vendor(respx_mock)
    invoke = CliRunner()
    for args in (["init"], ["init"], ["run", "search-volume", "--period", "2026-02"]):
        result = invoke.invoke(cli.app, [*args, "--config", str(path)])
        assert result.exit_code == 0, (result.output, result.exception)
    install(fake)
    result = invoke.invoke(cli.app, ["reports", "run", "--config", str(path)])
    assert result.exit_code == 0, (result.output, result.exception)
    assert len(fake.rows("bp_mart.search_volume_history_v1")) == 6
    assert vendor.post_calls == 1
    assert "example-password" not in result.output


def test_invalid_cli_config_never_connects(tmp_path, monkeypatch):
    config = yaml.safe_load((ROOT / "example.yaml").read_text())
    config["project_id"] = "unsafe;project"
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config))

    def denied(*args):
        pytest.fail("invalid configuration reached connect")

    import pytest

    monkeypatch.setattr(cli, "connect", denied)
    result = CliRunner().invoke(cli.app, ["init", "--config", str(path)])
    assert result.exit_code != 0


@pytest.mark.parametrize("period", ["2026-13", "2026-00", "0000-01", "2026-1", "wrong", ""])
def test_malformed_period_is_invalid_usage(period, monkeypatch, tmp_path):
    def denied(*args):
        pytest.fail("invalid period reached credentials or connect")

    monkeypatch.setattr(cli, "credentials", denied)
    monkeypatch.setattr(cli, "connect", denied)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(configuration()))
    result = CliRunner().invoke(cli.app, ["run", "search-volume", "--period", period,
                                        "--config", str(path)])
    assert result.exit_code == 2, result.exception
    assert "Invalid value" in result.output
    assert "--period" in result.output
    assert "YYYY-MM" in result.output


def test_example_client_id_has_a_clear_cli_error(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["brand-presence", "init", "--config",
                                    str(ROOT / "example.yaml")])
    with pytest.raises(SystemExit) as raised:
        cli.start()
    assert raised.value.code == 1
    assert "set client_id to your own stable identifier" in capsys.readouterr().err
