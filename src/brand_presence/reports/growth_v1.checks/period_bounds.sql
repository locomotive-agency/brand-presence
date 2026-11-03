SELECT
  growth.period_type, growth.period_start, growth.period_end, growth.client_brand_id,
  growth.country_code, growth.geo_id, growth.term_type, growth.brand
FROM mart.growth_v1 AS growth
WHERE growth.period_type NOT IN ('month', 'quarter', 'half', 'year', 'rolling_3', 'rolling_6',
    'rolling_12')
  OR growth.period_start != DATE_TRUNC(growth.period_start, MONTH)
  OR growth.period_end != DATE_ADD(growth.period_start, INTERVAL (CASE
      WHEN growth.period_type = 'month' THEN 0
      WHEN growth.period_type IN ('quarter', 'rolling_3') THEN 2
      WHEN growth.period_type IN ('half', 'rolling_6') THEN 5
      ELSE 11
    END) MONTH)
  OR (growth.period_type = 'quarter'
    AND MOD(EXTRACT(MONTH FROM growth.period_start), 3) != 1)
  OR (growth.period_type = 'half' AND EXTRACT(MONTH FROM growth.period_start) NOT IN (1, 7))
  OR (growth.period_type = 'year' AND EXTRACT(MONTH FROM growth.period_start) != 1)
