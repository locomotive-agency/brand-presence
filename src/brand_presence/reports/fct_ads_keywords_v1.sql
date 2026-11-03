WITH keywords AS (
  SELECT
    keyword.client_brand_id,
    keyword.ad_account_id,
    keyword.campaign_id,
    keyword.keyword_text,
    keyword.match_type,
    DATE_TRUNC(keyword.date, MONTH) AS month,
    keyword.impressions
  FROM stg.google_ads_keywords AS keyword
  QUALIFY DENSE_RANK() OVER (
    PARTITION BY keyword.client_brand_id, keyword.ad_account_id, keyword.date
    ORDER BY keyword.pulled_at DESC, keyword.period DESC
  ) = 1
)
SELECT
  keywords.month,
  keywords.client_brand_id,
  keywords.ad_account_id,
  keywords.campaign_id,
  keywords.keyword_text,
  keywords.match_type,
  SUM(keywords.impressions) AS impressions
FROM keywords
GROUP BY keywords.month, keywords.client_brand_id, keywords.ad_account_id,
  keywords.campaign_id, keywords.keyword_text, keywords.match_type
