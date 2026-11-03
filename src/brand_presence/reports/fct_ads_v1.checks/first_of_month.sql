SELECT
  fact.month, fact.client_brand_id, fact.ad_account_id, fact.campaign_id, fact.country_code,
  fact.geo_level, fact.location_id, fact.location_type, fact.currency_code
FROM core.fct_ads_v1 AS fact
WHERE fact.pulled_at = @pulled_at
  AND fact.month != DATE_TRUNC(fact.month, MONTH)
