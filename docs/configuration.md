# Configuration

All setup lives in one YAML file. Start from `example.yaml`:

```sh
cp example.yaml config.yaml
```

Credentials never go in this file. Each source reads them from environment variables.
The file is validated on every command, and errors name the key that failed.

## Core keys

| Key | Required | Default | Rules |
| --- | --- | --- | --- |
| `project_id` | Yes | | Your Google Cloud project. Domain-scoped IDs such as `example.com:proj` are not supported. |
| `client_id` | Yes | | Starts with a lowercase letter. Lowercase letters, digits, `_` or `-`, up to 64 characters. `example` is rejected. |
| `location` | No | `US` | One BigQuery location for all six datasets. |
| `time_zone` | No | `UTC` | Decides the default period for each source. |
| `state_dir` | No | `.brand-presence` | Local raw files and locks, relative to the config file. |
| `datasets` | No | `bp_ref`, `bp_ops`, `bp_raw`, `bp_stg`, `bp_core`, `bp_mart` | Dataset name per layer. |
| `maximum_bytes_billed` | No | 20 GiB | Byte cap per BigQuery export query. See [export](bigquery-export.md#cost). |

### `client_id`

Request keys are `client_id/source/period/hash`. Changing `client_id` changes every key,
so the current period is paid for again. Keep it unchanged when resuming work.

## Brands, countries and keywords

```yaml
brands:
  - brand_id: aardvark
    name: Example Aardvark
    aliases: []
    domains: [aardvark.example]
countries:
  - country_code: NL
    language: nl
    canonical_name: Netherlands
benchmark_sets:
  - client_brand_id: aardvark
    country_code: NL
    competitors: [example-rival]
    keywords:
      - keyword: sample aardvark theepot
        term_type: brand
        brand_id: aardvark
      - keyword: voorbeeld theepot kopen
        term_type: generic
```

- **Brands.** `aliases` are other names to match. `domains` are lowercase bare domains,
  with no scheme or path.
- **Countries.** Use DataForSEO's country names and language codes. Regional languages
  such as `zh-TW` are passed through, and the vendor decides if they are available.
- **Benchmark sets.** One per client brand and country. Every competitor must also be
  listed under `brands`.
- **Keywords.** `term_type` is `brand`, `competitor` or `generic`. Brand and competitor
  keywords need a listed `brand_id`. Set `search_volume: false` to skip a keyword in
  DataForSEO. An optional `canonical_term` groups keywords for the Google Ads reports.

Search volume needs at least one enabled keyword. Other sources can use benchmark sets
without keywords.

## Source keys

Each source adds its own keys. Their guides explain each one.

| Key | Source | Guide |
| --- | --- | --- |
| `benchmark_sets[].prompts`, `locations` | Cloro | [Cloro](sources/cloro.md#setup) |
| `mention_filters`, `subreddits`, `mention_text_days` | Syften | [Syften](sources/syften.md#setup) |
| `peec_projects`, `peec_brands` | Peec | [Peec](sources/peec.md#setup) |
| `web_properties` | Search Console, GA4 | [Search Console](sources/search-console.md#setup), [GA4](sources/ga4.md#setup) |
| `web_properties[].route`, `project`, `dataset`, `pages` | BigQuery export | [Export](bigquery-export.md#configure-a-property) |
| `ad_accounts`, `modifiers` | Google Ads | [Google Ads](sources/google-ads.md#setup) |
| `locations` with `kind: city` | GA4, Google Ads | [GA4](sources/ga4.md#cities), [Google Ads](sources/google-ads.md#cities-and-regions) |

## Stable IDs

Local IDs such as `web_property_id`, `ad_account_id` and Peec `mapping` are bound to what
they point at on first load. To point at a different property, account or project, use
a new local ID. Changing the binding of an existing ID is refused.

## `init`

```sh
uv run brand-presence init --config config.yaml
```

`init` is safe to repeat. It:

- creates missing datasets and tables, without deleting data;
- adds supported upgrade columns to existing tables;
- sets mention text expiry.

It does not grant permissions, enable APIs or repair other incompatible schemas. Run it
again after upgrading the kit.
