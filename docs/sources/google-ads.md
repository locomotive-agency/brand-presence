# Google Ads

| | |
| --- | --- |
| Command | `run google-ads` |
| Credentials | `GOOGLE_ADS_CLIENT_ID`, `GOOGLE_ADS_CLIENT_SECRET`, `GOOGLE_ADS_REFRESH_TOKEN` |
| Optional | `GOOGLE_ADS_LOGIN_CUSTOMER_ID`, `GOOGLE_ADS_DEVELOPER_TOKEN` |
| Cost | Free API. Daily operation quotas apply. |
| Period | Last ended ISO week, `--period YYYY-Www` |
| Reports | `reports run --source google-ads`, `reports run --source paid` |

The kit only reads fixed reports. It never buys ads.

## Setup

```yaml
ad_accounts:
  - ad_account_id: aardvark-ads
    customer_id: "1000000001"
    client_brand_id: aardvark
```

| Field | Rules |
| --- | --- |
| `ad_account_id` | Your stable local ID. Reports use it instead of the customer ID. |
| `customer_id` | Ten digits, quoted, without hyphens. Find it in the Google Ads account selector. |
| `client_brand_id` | Optional. Must be a client brand in `benchmark_sets`. Otherwise `unknown`. |

A local ID cannot change its customer or client brand. Use a new ID.

### Google Cloud access

1. Enable the Google Ads API in the project that owns your OAuth client.
2. Apply for production access on that project's Google Ads API Overview page. Until it
   is approved, Google answers `CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION` for real
   accounts. If another project of yours already has access, create the desktop OAuth
   client there instead. The kit uses the OAuth client's project for Ads calls.
3. Give your Google user access to each ad account, directly or through a manager.
4. Add the `adwords` scope to the OAuth consent screen.

### Developer token

Google retired developer tokens on 9 September 2026. New users apply through Google
Cloud. Do not apply for a new token in API Center. `GOOGLE_ADS_DEVELOPER_TOKEN` stays
optional for existing tokens, and is only sent when set. See Google's
[developer-token migration](https://developers.google.com/google-ads/api/docs/api-policy/developer-token).

### Refresh token

1. Sign in with the `adwords` scope, as described in [Google sign-in](../google-auth.md).
   Google may ask for a passkey. See
   [single-user authentication](https://developers.google.com/google-ads/api/docs/oauth/single-user-authentication).
2. The resulting ADC JSON holds `client_id`, `client_secret` and `refresh_token`. Copy
   them into the environment variables below. Never paste the file into logs.

Google Ads reads these variables, not the ADC file. BigQuery, GA4 and Search Console
still use ADC.

```sh
export GOOGLE_ADS_CLIENT_ID='example-client-id'
export GOOGLE_ADS_CLIENT_SECRET='example-client-secret'
export GOOGLE_ADS_REFRESH_TOKEN='example-refresh-token'
# Only when you reach accounts through a manager account:
# export GOOGLE_ADS_LOGIN_CUSTOMER_ID='1000000002'
uv run brand-presence init --config config.yaml
uv run brand-presence run google-ads --config config.yaml
uv run brand-presence reports run --source google-ads --config config.yaml
```

`GOOGLE_ADS_LOGIN_CUSTOMER_ID` is the manager account, not the reporting account.

## Cities and regions

Add `locations` with `google-ads` in `sources` and a `google_criterion_id` from Google's
[geographic targets](https://developers.google.com/google-ads/api/reference/data/geotargets).
These are public location IDs, not account IDs. Regions also need `region_code`.

Unlisted places fold into `other`. Unmatched criteria are counted on the pull summary.
Keep country, region and city totals separate.

## Modifiers

Optional `modifiers` group brand searches by the first matching word:

```yaml
modifiers:
  - modifier: price
    match_order: 1
```

`match_order` is a unique positive number. Keywords can name a `canonical_term` from
the same benchmark set.

## What a run checks and reads

- Before collecting, it checks account identity, currency and time zone.
- Tokens stay in memory and never reach raw responses.
- An unclassified authorization refusal stops the run, so an unreachable week is not
  marked as done.
- The default reads the last ended ISO week, then up to 52 earlier weeks, with at most
  60 history requests per run. Every page is followed.
- Days use the account's time zone. Costs stay in the account's currency. No currency
  conversion.

## Cost and quotas

The [Google Ads API has no usage fee](https://developers.google.com/google-ads/api/docs/productionize/access-levels).
Daily operations depend on [access level](https://developers.google.com/google-ads/api/docs/api-policy/access-levels):

| Access level | Daily operations on production accounts |
| --- | --- |
| Explorer | 2,880 |
| Basic | 15,000 |
| Standard | No cap |

Test access only reaches test accounts.
[Service limits](https://developers.google.com/google-ads/api/docs/best-practices/quotas) still apply.

## Tables and reports

Each account writes campaigns, geographic delivery, keywords, search terms and pull
summaries to `stg.google_ads_*`. The report group builds:

| Table | Contents |
| --- | --- |
| `core.fct_ads_v1` | Monthly delivery by account, campaign and geography |
| `core.fct_ads_keywords_v1` | Keyword impressions with campaign labels |
| `core.fct_ads_search_terms_v1` | Named search terms and coverage |
| `core.fct_ads_auction_v1` | Auction counts and bounded-share flags |
| `mart.paid_history_v1` | National presence history and campaign labels |
| `mart.brand_modifier_monthly_v1` | Counts by first matching modifier |

- Google can revise conversions after a week ends. Successful weeks are not refreshed.
- Search terms are privacy-filtered and do not explain all spend.
- Auction shares at Google's bounds are flagged. Inferred counts are estimates.

`paid` adds `mart.paid_monthly_v1`. See [reports](../reports.md).
