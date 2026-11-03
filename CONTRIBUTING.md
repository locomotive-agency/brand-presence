# Contributing

Use Python 3.12 or newer and uv. From a checkout of this repository:

```sh
uv sync --locked
uv run ruff check .
uv run pytest
```

Tests use invented fixtures and block network access and Application Default Credentials.
No vendor accounts or Google Cloud project are needed. Run the public example with:

```sh
uv run brand-presence example --output example-output
```

Follow the surrounding Python style and the Ruff rules in `pyproject.toml`. Reuse the
shared collection, recovery and report code. Keep changes focused and add tests at
observable boundaries. Test expectations should use known figures, not repeat the
implementation's calculation.

Read `CONTEXT.md` and `docs/adr/` before changing behaviour. When changing report SQL,
review its checks and the [example expectations](src/brand_presence/example/README.md).
Explain any changed figures before updating expected CSVs. Keep fixtures invented,
use `.example` domains, and never include credentials or private account identifiers.

Open an issue for a proposed feature or send a focused pull request with the problem,
the resulting behaviour and the checks you ran. See [SECURITY.md](SECURITY.md) for
vulnerability reports.
