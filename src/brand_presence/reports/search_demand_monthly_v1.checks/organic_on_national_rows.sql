SELECT
  demand.month, demand.client_brand_id, demand.country_code, demand.geo_id, demand.geo_level,
  demand.term_type, demand.brand
FROM mart.search_demand_monthly_v1 AS demand
WHERE (
    demand.organic_clicks IS NOT NULL
    OR demand.organic_impressions IS NOT NULL
    OR demand.organic_position_weighted IS NOT NULL
    OR demand.organic_avg_position IS NOT NULL
    OR demand.organic_days_covered IS NOT NULL
  )
  AND (
    demand.geo_level != 'national'
    OR (demand.term_type = 'brand' AND demand.brand != demand.client_brand_id)
  )
