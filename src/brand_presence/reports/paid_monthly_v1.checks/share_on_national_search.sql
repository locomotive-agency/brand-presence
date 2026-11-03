SELECT
  paid.month, paid.client_brand_id, paid.country_code, paid.geo_id, paid.geo_level,
  paid.campaign_type, paid.currency_code
FROM mart.paid_monthly_v1 AS paid
WHERE (paid.geo_level != 'national'
    OR paid.campaign_type NOT IN ('Search (brand)', 'Search (category)',
      'Search (unlabelled)'))
  AND (paid.auction_impressions IS NOT NULL
    OR paid.eligible_impressions IS NOT NULL
    OR paid.impression_share IS NOT NULL
    OR paid.absolute_top_impressions IS NOT NULL
    OR paid.absolute_top_eligible_impressions IS NOT NULL
    OR paid.absolute_top_impression_share IS NOT NULL
    OR paid.rank_lost_impressions IS NOT NULL
    OR paid.rank_lost_eligible_impressions IS NOT NULL
    OR paid.rank_lost_impression_share IS NOT NULL
    OR paid.budget_lost_impressions IS NOT NULL
    OR paid.budget_lost_eligible_impressions IS NOT NULL
    OR paid.budget_lost_impression_share IS NOT NULL
    OR paid.impression_share_bounded IS NOT NULL)
