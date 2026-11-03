SELECT
  fact.month, fact.client_brand_id, fact.ad_account_id, fact.campaign_id, fact.keyword_text,
  fact.match_type
FROM core.fct_ads_keywords_v1 AS fact
WHERE fact.pulled_at = @pulled_at
  AND fact.month != DATE_TRUNC(fact.month, MONTH)
