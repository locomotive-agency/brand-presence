"""The local command line. Only connect after configuration and arguments are valid."""
from pathlib import Path
from typing import Annotated

import httpx
import typer
from pydantic import ValidationError

from brand_presence import runner, schema
from brand_presence.errors import UserError
from brand_presence.reports import runner as reports_runner
from brand_presence.reports.definition import GROUPS
from brand_presence.runs import Period, period_start, source_period
from brand_presence.settings import credentials, load
from brand_presence.sources.registry import SOURCES
from brand_presence.state import locked
from brand_presence.warehouse import connect

app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)
reports = typer.Typer(no_args_is_help=True)
app.add_typer(reports, name="reports")
Config = Annotated[Path, typer.Option("--config", "-c", help="YAML configuration file.")]


@app.command("setup-geo-targets")
def setup_geo_targets(dated: Annotated[str, typer.Option("--date")],
                      config: Config = Path("config.yaml")):
    """Download a dated official geo targets file into local state."""
    from brand_presence import geo_targets

    settings = load(config)
    with locked(settings.state_path), httpx.Client(timeout=60, follow_redirects=False) as http:
        held = geo_targets.download(settings, dated, http)
    typer.echo(f"Geo targets ready: {held.date}, {held.header.rows} rows.")


@app.command("check-access")
def check_access(config: Config = Path("config.yaml")):
    """Check export metadata access and required columns without reading rows."""
    from brand_presence import access_check

    settings = load(config)
    if not any(p.route == "export" for p in settings.web_properties):
        typer.echo("No export properties configured.")
        return
    checks = access_check.run(connect(settings), settings)
    for check in checks:
        typer.echo(check.line())
    typer.echo("Metadata check only. Query jobs also need Job User on the billing project.")
    if not all(check.ok for check in checks):
        raise typer.Exit(1)


@app.command("init")
def initialise(config: Config = Path("config.yaml")):
    """Create datasets and tables in the configured project."""
    settings = load(config)
    with locked(settings.state_path):
        schema.initialise(connect(settings))
    typer.echo("Datasets and tables are ready.")


@app.command()
def run(source: str, config: Config = Path("config.yaml"),
        period: Annotated[str | None, typer.Option(
            help="Month for search-volume; ISO week for AI/Google; date for mentions.")] = None):
    """Collect a source, recovering unfinished work first."""
    if source not in SOURCES:
        raise typer.BadParameter("source must be " + ", ".join(SOURCES))
    if source == "mentions" and period is not None:
        raise typer.BadParameter("mentions uses its saved cursor; omit --period",
                                 param_hint="--period")
    settings = load(config)
    try:
        named = Period(period, period_start(period)) if period is not None else None
    except ValueError:
        raise typer.BadParameter("period must be a valid YYYY-MM, YYYY-Www or YYYY-MM-DD",
                                 param_hint="--period") from None
    if named:
        import re
        from datetime import UTC, datetime

        form = (r"[0-9]{4}-[0-9]{2}" if source == "search-volume" else
                r"[0-9]{4}-W[0-9]{2}")
        if not re.fullmatch(form, named.name):
            raise typer.BadParameter("period has the wrong format for this source")
        if named.start > source_period(source, datetime.now(UTC), settings.time_zone).start:
            raise typer.BadParameter("period is not available yet")
    if source == "ai-answers" and not any(s.prompts for s in settings.benchmark_sets):
        raise UserError("ai-answers needs prompts in benchmark_sets")
    if source == "mentions" and not settings.mention_filters:
        raise UserError("mentions needs mention_filters in YAML")
    if source == "peec" and not settings.peec_projects:
        raise UserError("peec needs peec_projects and peec_brands in YAML")
    if source == "search-volume" and not any(
        k.search_volume for s in settings.benchmark_sets for k in s.keywords
    ):
        raise UserError("search-volume needs enabled keywords in benchmark_sets")
    if source == "google-ads" and not settings.ad_accounts:
        raise UserError("google-ads needs ad_accounts in YAML")
    if source in ("search-console", "ga4") and not any(
        p.source == source for p in settings.web_properties
    ):
        raise UserError(f"{source} needs web_properties in YAML")
    keys = credentials(source)
    bq = connect(settings)
    with httpx.Client(timeout=60, follow_redirects=False) as http:
        result = runner.run(SOURCES[source](), settings, runner.World(bq, http, keys), period=named)
    for line in result.lines:
        typer.echo(line)
    if result.source_counts:
        typer.echo(f"Parsing counts: {result.source_counts}")
    typer.echo(f"{result.status}: {result.counts}")
    raise typer.Exit(result.exit_code)


@reports.command("run")
def report_run(config: Config = Path("config.yaml"),
               source: Annotated[str, typer.Option()] = "search-volume"):
    """Build source facts and serving reports."""
    if source not in GROUPS:
        raise typer.BadParameter("source must be " + ", ".join(GROUPS))
    settings = load(config)
    for table in reports_runner.run(connect(settings), settings, source=source):
        typer.echo(f"Built {settings.table(table)}")


def start():
    try:
        app()
    except UserError as error:
        typer.echo(str(error), err=True)
        raise SystemExit(1) from None
    except ValidationError as error:
        for issue in error.errors(include_input=False, include_url=False):
            field = ".".join(str(part) for part in issue["loc"])
            reason = ("set client_id to your own stable identifier; example is reserved"
                      if field == "client_id" else issue["msg"])
            typer.echo(f"Invalid configuration: {field or 'document'} ({reason})", err=True)
        raise SystemExit(1) from None
    except Exception as error:
        typer.echo(f"Command failed: {type(error).__name__}. "
                   "Check configuration and access.", err=True)
        raise SystemExit(1) from None
