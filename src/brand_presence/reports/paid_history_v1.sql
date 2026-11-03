WITH delivery_latest AS (
  SELECT
    latest.month,
    latest.client_brand_id,
    latest.ad_account_id,
    latest.campaign_id,
    latest.country_code,
    latest.geo_level,
    latest.location_id,
    latest.location_type,
    latest.currency_code,
    MAX(latest.pulled_at) AS pulled_at
  FROM core.fct_ads_v1 AS latest
  GROUP BY latest.month, latest.client_brand_id, latest.ad_account_id, latest.campaign_id,
    latest.country_code, latest.geo_level, latest.location_id, latest.location_type,
    latest.currency_code
),
delivery AS (
  SELECT
    fact.month,
    fact.client_brand_id,
    fact.ad_account_id,
    fact.campaign_id,
    fact.country_code,
    fact.geo_level,
    fact.location_id,
    fact.currency_code,
    fact.channel_type,
    fact.impressions,
    fact.clicks,
    fact.cost_micros,
    fact.pulled_at
  FROM core.fct_ads_v1 AS fact
  JOIN delivery_latest
    ON delivery_latest.month = fact.month
    AND delivery_latest.client_brand_id = fact.client_brand_id
    AND delivery_latest.ad_account_id = fact.ad_account_id
    AND delivery_latest.campaign_id = fact.campaign_id
    AND delivery_latest.country_code = fact.country_code
    AND delivery_latest.geo_level = fact.geo_level
    AND delivery_latest.location_id = fact.location_id
    AND delivery_latest.location_type = fact.location_type
    AND delivery_latest.currency_code = fact.currency_code
    AND delivery_latest.pulled_at = fact.pulled_at
  WHERE fact.location_type = 'presence'
    AND fact.geo_level = 'country'
),
keyword_latest AS (
  SELECT
    latest_keyword.month,
    latest_keyword.client_brand_id,
    latest_keyword.ad_account_id,
    latest_keyword.campaign_id,
    latest_keyword.keyword_text,
    latest_keyword.match_type,
    MAX(latest_keyword.pulled_at) AS pulled_at
  FROM core.fct_ads_keywords_v1 AS latest_keyword
  GROUP BY latest_keyword.month, latest_keyword.client_brand_id, latest_keyword.ad_account_id,
    latest_keyword.campaign_id, latest_keyword.keyword_text, latest_keyword.match_type
),
bids AS (
  SELECT
    bid.month,
    bid.client_brand_id,
    bid.ad_account_id,
    bid.campaign_id,
    bid.keyword_text,
    bid.match_type,
    bid.impressions
  FROM core.fct_ads_keywords_v1 AS bid
  JOIN keyword_latest
    ON keyword_latest.month = bid.month
    AND keyword_latest.client_brand_id = bid.client_brand_id
    AND keyword_latest.ad_account_id = bid.ad_account_id
    AND keyword_latest.campaign_id = bid.campaign_id
    AND keyword_latest.keyword_text = bid.keyword_text
    AND keyword_latest.match_type = bid.match_type
    AND keyword_latest.pulled_at = bid.pulled_at
),
client_names AS (
  SELECT
    named.brand_id,
    CONCAT(' ', TRIM(REGEXP_REPLACE(LOWER(named.name), '[^\\p{L}\\p{N}]+', ' ')), ' ') AS words
  FROM ref.current_brands AS named
  UNION ALL
  SELECT
    aliased.brand_id,
    CONCAT(' ', TRIM(REGEXP_REPLACE(LOWER(other_name), '[^\\p{L}\\p{N}]+', ' ')), ' ') AS words
  FROM ref.current_brands AS aliased
  CROSS JOIN UNNEST(aliased.aliases) AS other_name
  UNION ALL
  SELECT
    own_keyword.client_brand_id AS brand_id,
    CONCAT(' ', TRIM(REGEXP_REPLACE(LOWER(own_keyword.keyword), '[^\\p{L}\\p{N}]+', ' ')), ' ')
      AS words
  FROM ref.current_benchmark_keywords AS own_keyword
  LEFT JOIN ref.current_benchmark_keywords AS canonical_keyword
    ON canonical_keyword.client_brand_id = own_keyword.client_brand_id
    AND canonical_keyword.country_code = own_keyword.country_code
    AND canonical_keyword.keyword = own_keyword.canonical_term
  WHERE own_keyword.term_type = 'brand'
    AND COALESCE(canonical_keyword.brand_id, own_keyword.brand_id) = own_keyword.client_brand_id
),
brand_bids AS (
  SELECT DISTINCT
    naming.month,
    naming.client_brand_id,
    naming.ad_account_id,
    naming.campaign_id,
    naming.keyword_text,
    naming.match_type,
    naming.impressions
  FROM bids AS naming
  JOIN client_names
    ON client_names.brand_id = naming.client_brand_id
    AND client_names.words != '  '
    AND STRPOS(
      CONCAT(' ', TRIM(REGEXP_REPLACE(LOWER(naming.keyword_text), '[^\\p{L}\\p{N}]+', ' ')),
        ' '),
      client_names.words) > 0
),
bid_totals AS (
  SELECT
    every_bid.month,
    every_bid.client_brand_id,
    every_bid.ad_account_id,
    every_bid.campaign_id,
    SUM(every_bid.impressions) AS impressions
  FROM bids AS every_bid
  GROUP BY every_bid.month, every_bid.client_brand_id, every_bid.ad_account_id,
    every_bid.campaign_id
),
brand_totals AS (
  SELECT
    brand_bids.month,
    brand_bids.client_brand_id,
    brand_bids.ad_account_id,
    brand_bids.campaign_id,
    SUM(brand_bids.impressions) AS impressions
  FROM brand_bids
  GROUP BY brand_bids.month, brand_bids.client_brand_id, brand_bids.ad_account_id,
    brand_bids.campaign_id
),
campaign_months AS (
  SELECT
    ran.month,
    ran.client_brand_id,
    ran.ad_account_id,
    ran.campaign_id,
    MAX(ran.channel_type) AS channel_type
  FROM delivery AS ran
  GROUP BY ran.month, ran.client_brand_id, ran.ad_account_id, ran.campaign_id
),
labels AS (
  SELECT
    campaign_months.month,
    campaign_months.client_brand_id,
    campaign_months.ad_account_id,
    campaign_months.campaign_id,
    CASE
      WHEN campaign_months.channel_type = 'SEARCH'
        AND (bid_totals.impressions IS NULL OR bid_totals.impressions = 0)
        THEN 'Search (unlabelled)'
      WHEN campaign_months.channel_type = 'SEARCH'
        AND COALESCE(brand_totals.impressions, 0) * 2 > bid_totals.impressions
        THEN 'Search (brand)'
      WHEN campaign_months.channel_type = 'SEARCH' THEN 'Search (category)'
      WHEN campaign_months.channel_type = 'PERFORMANCE_MAX' THEN 'Performance Max'
      WHEN campaign_months.channel_type = 'VIDEO' THEN 'Video'
      WHEN campaign_months.channel_type = 'DISPLAY' THEN 'Display'
      WHEN campaign_months.channel_type = 'DEMAND_GEN' THEN 'Demand Gen'
      ELSE COALESCE(campaign_months.channel_type, 'unknown')
    END AS campaign_type
  FROM campaign_months
  LEFT JOIN bid_totals
    ON bid_totals.month = campaign_months.month
    AND bid_totals.client_brand_id = campaign_months.client_brand_id
    AND bid_totals.ad_account_id = campaign_months.ad_account_id
    AND bid_totals.campaign_id = campaign_months.campaign_id
  LEFT JOIN brand_totals
    ON brand_totals.month = campaign_months.month
    AND brand_totals.client_brand_id = campaign_months.client_brand_id
    AND brand_totals.ad_account_id = campaign_months.ad_account_id
    AND brand_totals.campaign_id = campaign_months.campaign_id
)
SELECT
  delivery.month,
  delivery.client_brand_id,
  delivery.country_code,
  delivery.location_id AS geo_id,
  'national' AS geo_level,
  labels.campaign_type,
  delivery.currency_code,
  SUM(delivery.impressions) AS impressions,
  SUM(delivery.clicks) AS clicks,
  CAST(SUM(delivery.cost_micros) AS NUMERIC) / 1000000 AS cost,
  MAX(delivery.pulled_at) AS pulled_at
FROM delivery
JOIN labels
  ON labels.month = delivery.month
  AND labels.client_brand_id = delivery.client_brand_id
  AND labels.ad_account_id = delivery.ad_account_id
  AND labels.campaign_id = delivery.campaign_id
GROUP BY delivery.month, delivery.client_brand_id, delivery.country_code,
  delivery.location_id, labels.campaign_type, delivery.currency_code
