WITH terms AS (
  SELECT
    searched.client_brand_id,
    searched.ad_account_id,
    searched.campaign_id,
    searched.search_term,
    DATE_TRUNC(searched.date, MONTH) AS month,
    searched.impressions,
    searched.clicks,
    searched.conversions
  FROM stg.google_ads_search_terms AS searched
  QUALIFY DENSE_RANK() OVER (
    PARTITION BY searched.client_brand_id, searched.ad_account_id, searched.date
    ORDER BY searched.pulled_at DESC, searched.period DESC
  ) = 1
),
coverage AS (
  SELECT
    pull.ad_account_id,
    DATE_TRUNC(covered_day, MONTH) AS month,
    COUNT(DISTINCT covered_day) AS days_covered
  FROM stg.google_ads_pulls AS pull
  CROSS JOIN UNNEST(GENERATE_DATE_ARRAY(pull.window_start, pull.window_end)) AS covered_day
  WHERE pull.report = 'search_terms'
  GROUP BY pull.ad_account_id, month
),
summed AS (
  SELECT
    terms.month,
    terms.client_brand_id,
    terms.ad_account_id,
    terms.campaign_id,
    terms.search_term,
    SUM(terms.impressions) AS impressions,
    SUM(terms.clicks) AS clicks,
    SUM(CAST(terms.conversions AS NUMERIC)) AS conversions
  FROM terms
  GROUP BY terms.month, terms.client_brand_id, terms.ad_account_id, terms.campaign_id,
    terms.search_term
)
SELECT
  summed.month,
  summed.client_brand_id,
  summed.ad_account_id,
  summed.campaign_id,
  summed.search_term,
  summed.impressions,
  summed.clicks,
  summed.conversions,
  coverage.days_covered
FROM summed
LEFT JOIN coverage
  ON coverage.ad_account_id = summed.ad_account_id
  AND coverage.month = summed.month
