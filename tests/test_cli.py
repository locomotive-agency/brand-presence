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


def test_mentions_rejects_period_before_connecting(tmp_path, monkeypatch):
    def denied(*args):
        pytest.fail('mentions with --period reached credentials or connect')

    monkeypatch.setattr(cli, 'credentials', denied)
    monkeypatch.setattr(cli, 'connect', denied)
    path = tmp_path / 'config.yaml'
    path.write_text(yaml.safe_dump(configuration()))
    result = CliRunner().invoke(cli.app, [
        'run', 'mentions', '--period', '2026-03-02', '--config', str(path),
    ])
    assert result.exit_code == 2, result.exception
    assert '--period' in result.output
    assert 'mentions uses its saved cursor' in result.output


@pytest.mark.parametrize('source,env_name,period', [
    ('ai-answers', 'CLORO_API_KEY', '2026-W10'),
    ('mentions', 'SYFTEN_API_KEY', None),
    ('peec', 'PEEC_API_KEY', '2026-W09'),
])
def test_ported_sources_from_cli(tmp_path, respx_mock, monkeypatch, source, env_name, period):
    from peec_project import prepared as peec_project
    from reporting import install_sources
    from test_cloro_workflow import prepared as cloro_project
    from test_mentions_workflow import prepared as mentions_project

    prepare = {'ai-answers': cloro_project, 'mentions': mentions_project, 'peec': peec_project}
    project, _ = prepare[source](tmp_path, respx_mock)
    path = tmp_path / 'config.yaml'
    path.write_text(yaml.safe_dump(project.settings.model_dump(mode='json')))
    monkeypatch.setattr(cli, 'connect', lambda _: project.bq)
    monkeypatch.setenv(env_name, 'example-key')
    world = cli.runner.World
    monkeypatch.setattr(cli.runner, 'World', lambda *args: world(
        *args, clock=lambda: project.now, sleep=lambda _: None, wait_seconds=60))
    invoke = CliRunner()
    args = ['run', source, '--config', str(path)]
    if period is not None:
        args += ['--period', period]
    result = invoke.invoke(cli.app, args)
    assert result.exit_code == 0, (result.output, result.exception)
    install_sources(project.fake, project.settings)
    result = invoke.invoke(cli.app, ['reports', 'run', '--source', source, '--config', str(path)])
    assert result.exit_code == 0, (result.output, result.exception)
    assert 'example-key' not in result.output


def test_sigterm_unwinds_like_ctrl_c_so_open_transactions_roll_back(monkeypatch, capsys):
    import os
    import signal
    from contextlib import contextmanager

    from brand_presence import cli

    ended = []

    @contextmanager
    def transaction():
        try:
            yield
        except BaseException as error:
            ended.append(type(error).__name__)
            raise

    def killed_mid_write():
        with transaction():
            os.kill(os.getpid(), signal.SIGTERM)

    previous = signal.getsignal(signal.SIGTERM)
    monkeypatch.setattr(cli, "app", killed_mid_write)
    try:
        with pytest.raises(SystemExit) as stopped:
            cli.start()
    finally:
        signal.signal(signal.SIGTERM, previous)
    assert ended == ["KeyboardInterrupt"]
    assert stopped.value.code == 130
    assert "Interrupted" in capsys.readouterr().err
