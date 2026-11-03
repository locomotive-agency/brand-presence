# Reports

Reports are versioned SQL with checks, built in your BigQuery project. Each group
builds its prerequisites and runs its checks in one transaction. A failed build rolls
back.

```sh
uv run brand-presence reports run --source GROUP --config config.yaml
```

Without `--source`, the group is `search-volume`.

## Groups

| Group | Reports | Guide |
| --- | --- | --- |
| `search-volume` | `core.fct_search_volume_v1`, `mart.search_volume_history_v1` | [DataForSEO](sources/dataforseo.md#reports) |
| `ai-answers` | Five Cloro reports | [Cloro](sources/cloro.md#reports) |
| `mentions` | `core.fct_mentions_v1` | [Syften](sources/syften.md#tables-and-reports) |
| `peec` | Five Peec reports | [Peec](sources/peec.md#tables-and-reports) |
| `ai-weekly` | Cloro and Peec reports, plus three weekly marts | [Below](#weekly-ai-reports) |
| `search-console` | `core.fct_gsc_v1`, `core.fct_gsc_export_v1` | [Search Console](sources/search-console.md#tables-and-reports) |
| `ga4` | API and export facts, engagement and page reports | [GA4](sources/ga4.md#tables-and-reports) |
| `google-ads` | Six Ads reports | [Google Ads](sources/google-ads.md#tables-and-reports) |
| `search-demand` | `mart.search_demand_monthly_v1`, `mart.growth_v1` | [Below](#cross-source-reports) |
| `paid` | `mart.paid_monthly_v1` | [Below](#cross-source-reports) |

## Weekly AI reports

```sh
uv run brand-presence reports run --source ai-weekly --config config.yaml
```

Builds the five Cloro and five Peec reports, then:

- `mart.ai_brand_mentions_weekly_v1`
- `mart.ai_domain_citations_weekly_v1`
- `mart.ai_overview_presence_weekly_v1`

All 13 builds share one transaction, and empty source tables are allowed.

- Source and engine stay separate.
- Countries and locations are combined within a project.
- Only observed weeks appear.
- Keyword groups without a label are `unknown`.
- Pages with no AI overview are counted apart from answered prompts.

## Cross-source reports

- **`search-demand`** starts from configured keyword volume and adds Search Console, from
  the API or the export. Collect search volume first to give it rows. `growth_v1`
  aggregates those months.
- **`paid`** adds Google Ads to search demand. It builds with no search-demand rows, and
  its volume comparisons then stay null. It works without any export configured.

Missing source data stays empty or null.

## Reading reports safely

- An empty report does not prove a brand had no activity.
- Nulls are not zeros.
- Distinct users and ratios cannot be added across rows.
- Sentiment and recommendation labels are never inferred.

## Reports not included

These need data the kit does not collect yet. Their SQL was left out whole, not trimmed.

| Reports | Missing dependency |
| --- | --- |
| `fct_crm_leads_v1`, `fct_crm_deals_v1`, `pipeline_monthly_v1` | CRM |
| `correlation_v1` | CRM pipeline reports |
| `competitor_ads_history_v1` | Ads Transparency |
| `fct_serp_v1`, `serp_history_v1`, `serp_presence_monthly_v1` | Search results and paid search-result ads |
| `competitor_ads_v1` | Search-result facts and ad labels |
| `fct_trends_v1`, `search_trends_history_v1` | Trends |
| `freshness_v2` | CRM, Trends, search results and source-freshness data |
| `reddit_mentions_v1`, `reddit_mentions_v2` | External mention labels |

## Testing

Report SQL runs in tests through a local SQL engine against invented data. Those tests
do not replace a first check on real BigQuery.
