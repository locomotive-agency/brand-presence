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
    fact.conversions,
    fact.days_covered,
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
    AND fact.month >= DATE '2024-01-01'
),
places AS (
  SELECT
    listed.location_id,
    listed.kind,
    CASE
      WHEN listed.kind = 'country' THEN 'national'
      WHEN listed.kind = 'region' THEN 'state'
      ELSE 'city'
    END AS place_level
  FROM ref.current_locations AS listed
  WHERE listed.kind IN ('country', 'region', 'city')
    AND STRPOS(CONCAT(',', ARRAY_TO_STRING(listed.sources, ','), ','), ',google-ads,') > 0
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
),
delivered AS (
  SELECT
    delivery.month,
    delivery.client_brand_id,
    delivery.country_code,
    delivery.location_id AS geo_id,
    places.place_level,
    places.kind AS report_level,
    labels.campaign_type,
    delivery.currency_code,
    SUM(delivery.impressions) AS impressions,
    SUM(delivery.clicks) AS clicks,
    SUM(delivery.cost_micros) AS cost_micros,
    SUM(delivery.conversions) AS conversions,
    COALESCE(MIN(delivery.days_covered), 0) AS days_covered,
    MAX(delivery.pulled_at) AS pulled_at
  FROM delivery
  JOIN places
    ON places.location_id = delivery.location_id
    AND places.kind = delivery.geo_level
  JOIN labels
    ON labels.month = delivery.month
    AND labels.client_brand_id = delivery.client_brand_id
    AND labels.ad_account_id = delivery.ad_account_id
    AND labels.campaign_id = delivery.campaign_id
  GROUP BY delivery.month, delivery.client_brand_id, delivery.country_code, delivery.location_id,
    places.place_level, places.kind, labels.campaign_type, delivery.currency_code
),
account_coverage AS (
  SELECT
    pull.ad_account_id,
    pull.report,
    DATE_TRUNC(covered_day, MONTH) AS month,
    COUNT(DISTINCT covered_day) AS days_covered
  FROM stg.google_ads_pulls AS pull
  CROSS JOIN UNNEST(GENERATE_DATE_ARRAY(pull.window_start, pull.window_end)) AS covered_day
  WHERE pull.report IN ('geography', 'geography_regions', 'geography_cities')
  GROUP BY pull.ad_account_id, pull.report, DATE_TRUNC(covered_day, MONTH)
),
brand_accounts AS (
  SELECT
    account.ad_account_id,
    COALESCE(account.client_brand_id, 'unknown') AS client_brand_id
  FROM ref.current_ad_accounts AS account
),
month_levels AS (
  SELECT DISTINCT
    reported.month,
    reported.client_brand_id,
    reported.geo_level,
    CASE
      WHEN reported.geo_level = 'country' THEN 'geography'
      WHEN reported.geo_level = 'region' THEN 'geography_regions'
      ELSE 'geography_cities'
    END AS report
  FROM delivery AS reported
),
brand_coverage AS (
  SELECT
    month_levels.month,
    month_levels.client_brand_id,
    month_levels.geo_level,
    MIN(COALESCE(account_coverage.days_covered, 0)) AS days_covered
  FROM month_levels
  JOIN brand_accounts
    ON brand_accounts.client_brand_id = month_levels.client_brand_id
  LEFT JOIN account_coverage
    ON account_coverage.ad_account_id = brand_accounts.ad_account_id
    AND account_coverage.report = month_levels.report
    AND account_coverage.month = month_levels.month
  GROUP BY month_levels.month, month_levels.client_brand_id, month_levels.geo_level
),
campaign_countries AS (
  SELECT
    national.month,
    national.client_brand_id,
    national.ad_account_id,
    national.campaign_id,
    national.country_code,
    SUM(national.impressions) AS impressions
  FROM delivery AS national
  JOIN places
    ON places.location_id = national.location_id
    AND places.kind = 'country'
  WHERE national.geo_level = 'country'
  GROUP BY national.month, national.client_brand_id, national.ad_account_id,
    national.campaign_id, national.country_code
),
campaign_most AS (
  SELECT
    most.month,
    most.client_brand_id,
    most.ad_account_id,
    most.campaign_id,
    MAX(most.impressions) AS impressions
  FROM campaign_countries AS most
  GROUP BY most.month, most.client_brand_id, most.ad_account_id, most.campaign_id
),
campaign_home AS (
  SELECT
    candidate.month,
    candidate.client_brand_id,
    candidate.ad_account_id,
    candidate.campaign_id,
    MIN(candidate.country_code) AS country_code
  FROM campaign_countries AS candidate
  JOIN campaign_most
    ON campaign_most.month = candidate.month
    AND campaign_most.client_brand_id = candidate.client_brand_id
    AND campaign_most.ad_account_id = candidate.ad_account_id
    AND campaign_most.campaign_id = candidate.campaign_id
    AND campaign_most.impressions = candidate.impressions
  GROUP BY candidate.month, candidate.client_brand_id, candidate.ad_account_id,
    candidate.campaign_id
),
auction_latest AS (
  SELECT
    latest_auction.month,
    latest_auction.client_brand_id,
    latest_auction.ad_account_id,
    latest_auction.campaign_id,
    latest_auction.currency_code,
    MAX(latest_auction.pulled_at) AS pulled_at
  FROM core.fct_ads_auction_v1 AS latest_auction
  GROUP BY latest_auction.month, latest_auction.client_brand_id, latest_auction.ad_account_id,
    latest_auction.campaign_id, latest_auction.currency_code
),
auction AS (
  SELECT
    campaign_home.month,
    campaign_home.client_brand_id,
    campaign_home.country_code,
    labels.campaign_type,
    counted.currency_code,
    SUM(counted.auction_impressions) AS auction_impressions,
    SUM(counted.eligible_impressions) AS eligible_impressions,
    SUM(counted.absolute_top_impressions) AS absolute_top_impressions,
    SUM(counted.absolute_top_eligible_impressions) AS absolute_top_eligible_impressions,
    SUM(counted.rank_lost_impressions) AS rank_lost_impressions,
    SUM(counted.rank_lost_eligible_impressions) AS rank_lost_eligible_impressions,
    SUM(counted.budget_lost_impressions) AS budget_lost_impressions,
    SUM(counted.budget_lost_eligible_impressions) AS budget_lost_eligible_impressions,
    LOGICAL_OR(counted.impression_share_bounded) AS impression_share_bounded,
    MAX(counted.pulled_at) AS pulled_at
  FROM core.fct_ads_auction_v1 AS counted
  JOIN auction_latest
    ON auction_latest.month = counted.month
    AND auction_latest.client_brand_id = counted.client_brand_id
    AND auction_latest.ad_account_id = counted.ad_account_id
    AND auction_latest.campaign_id = counted.campaign_id
    AND auction_latest.currency_code = counted.currency_code
    AND auction_latest.pulled_at = counted.pulled_at
  JOIN campaign_home
    ON campaign_home.month = counted.month
    AND campaign_home.client_brand_id = counted.client_brand_id
    AND campaign_home.ad_account_id = counted.ad_account_id
    AND campaign_home.campaign_id = counted.campaign_id
  JOIN labels
    ON labels.month = counted.month
    AND labels.client_brand_id = counted.client_brand_id
    AND labels.ad_account_id = counted.ad_account_id
    AND labels.campaign_id = counted.campaign_id
  WHERE labels.campaign_type IN ('Search (brand)', 'Search (category)', 'Search (unlabelled)')
  GROUP BY campaign_home.month, campaign_home.client_brand_id, campaign_home.country_code,
    labels.campaign_type, counted.currency_code
),
brand_volume AS (
  SELECT
    demand.month,
    demand.client_brand_id,
    demand.country_code,
    demand.geo_id,
    demand.search_volume
  FROM mart.search_demand_monthly_v1 AS demand
  WHERE demand.term_type = 'brand'
    AND demand.brand = demand.client_brand_id
)
SELECT
  delivered.month,
  delivered.client_brand_id,
  delivered.country_code,
  delivered.geo_id,
  delivered.place_level AS geo_level,
  delivered.campaign_type,
  delivered.currency_code,
  delivered.impressions,
  delivered.clicks,
  CAST(delivered.cost_micros AS NUMERIC) / 1000000 AS cost,
  SAFE_DIVIDE(CAST(delivered.cost_micros AS FLOAT64) / 1000000, delivered.clicks) AS avg_cpc,
  delivered.conversions,
  auction.auction_impressions,
  auction.eligible_impressions,
  SAFE_DIVIDE(auction.auction_impressions, auction.eligible_impressions) AS impression_share,
  auction.absolute_top_impressions,
  auction.absolute_top_eligible_impressions,
  SAFE_DIVIDE(auction.absolute_top_impressions, auction.absolute_top_eligible_impressions)
    AS absolute_top_impression_share,
  auction.rank_lost_impressions,
  auction.rank_lost_eligible_impressions,
  SAFE_DIVIDE(auction.rank_lost_impressions, auction.rank_lost_eligible_impressions)
    AS rank_lost_impression_share,
  auction.budget_lost_impressions,
  auction.budget_lost_eligible_impressions,
  SAFE_DIVIDE(auction.budget_lost_impressions, auction.budget_lost_eligible_impressions)
    AS budget_lost_impression_share,
  auction.impression_share_bounded,
  IF(delivered.campaign_type = 'Search (brand)',
    delivered.impressions > brand_volume.search_volume, NULL) AS exceeds_brand_volume,
  delivered.month < DATE_TRUNC(CURRENT_DATE(@client_time_zone), MONTH)
    AND delivered.days_covered
      = DATE_DIFF(DATE_ADD(delivered.month, INTERVAL 1 MONTH), delivered.month, DAY)
    AND COALESCE(brand_coverage.days_covered, delivered.days_covered)
      = DATE_DIFF(DATE_ADD(delivered.month, INTERVAL 1 MONTH), delivered.month, DAY)
    AS is_complete_month,
  GREATEST(delivered.pulled_at, COALESCE(auction.pulled_at, delivered.pulled_at)) AS pulled_at
FROM delivered
LEFT JOIN auction
  ON delivered.place_level = 'national'
  AND auction.month = delivered.month
  AND auction.client_brand_id = delivered.client_brand_id
  AND auction.country_code = delivered.country_code
  AND auction.campaign_type = delivered.campaign_type
  AND auction.currency_code = delivered.currency_code
LEFT JOIN brand_coverage
  ON brand_coverage.month = delivered.month
  AND brand_coverage.client_brand_id = delivered.client_brand_id
  AND brand_coverage.geo_level = delivered.report_level
LEFT JOIN brand_volume
  ON brand_volume.month = delivered.month
  AND brand_volume.client_brand_id = delivered.client_brand_id
  AND brand_volume.country_code = delivered.country_code
  AND brand_volume.geo_id = delivered.geo_id
