SELECT
  demand.month, demand.client_brand_id, demand.country_code, demand.geo_id, demand.term_type,
  demand.brand
FROM mart.search_demand_monthly_v1 AS demand
WHERE demand.term_type != 'brand'
  AND (demand.share_of_search IS NOT NULL OR demand.brand_set_volume IS NOT NULL)
