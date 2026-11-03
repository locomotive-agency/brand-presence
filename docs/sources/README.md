# Sources

| Source | Collect | Period | Guide |
| --- | --- | --- | --- |
| DataForSEO | `run search-volume` | Month | [dataforseo.md](dataforseo.md) |
| Cloro | `run ai-answers` | ISO week, current | [cloro.md](cloro.md) |
| Syften | `run mentions` | Saved cursor | [syften.md](syften.md) |
| Peec | `run peec` | ISO week, last complete | [peec.md](peec.md) |
| Search Console | `run search-console` | ISO week, last ended | [search-console.md](search-console.md) |
| GA4 | `run ga4` | ISO week, last ended | [ga4.md](ga4.md) |
| Google Ads | `run google-ads` | ISO week, last ended | [google-ads.md](google-ads.md) |

Every source follows the same pattern:

```sh
export SOURCE_CREDENTIALS=...
uv run brand-presence init --config config.yaml
uv run brand-presence run SOURCE --config config.yaml
uv run brand-presence reports run --source SOURCE --config config.yaml
```

Use `--period` to resume a specific period. Each guide gives its format.
