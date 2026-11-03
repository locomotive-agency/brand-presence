SELECT
  fact.month, fact.client_brand_id, fact.ad_account_id, fact.campaign_id
FROM core.fct_ads_search_terms_v1 AS fact
WHERE fact.pulled_at = @pulled_at
  AND fact.month != DATE_TRUNC(fact.month, MONTH)
