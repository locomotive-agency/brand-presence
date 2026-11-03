SELECT
  growth.period_type, growth.period_start, growth.client_brand_id, growth.country_code,
  growth.geo_id, growth.term_type, growth.brand, growth.period_label, growth.is_partial
FROM mart.growth_v1 AS growth
WHERE (growth.is_partial AND growth.period_label NOT LIKE '% (partial)')
  OR (NOT growth.is_partial AND growth.period_label LIKE '% (partial)')
