-- All retained months, including backfilled months; latest fact revision per grain.
WITH latest AS (
  SELECT f.month, f.client_brand_id, f.country_code, f.location_id, f.keyword, MAX(f.pulled_at) AS pulled_at
  FROM core.fct_search_volume_v1 AS f
  GROUP BY f.month, f.client_brand_id, f.country_code, f.location_id, f.keyword
)
SELECT fact.month, fact.client_brand_id, fact.country_code, fact.location_id, fact.keyword, fact.term_type, fact.subject_brand_id, fact.benchmark_version, fact.search_volume, fact.pulled_at
FROM core.fct_search_volume_v1 AS fact
JOIN latest ON latest.month = fact.month
  AND latest.client_brand_id = fact.client_brand_id
  AND latest.country_code = fact.country_code
  AND latest.location_id = fact.location_id
  AND latest.keyword = fact.keyword
  AND latest.pulled_at = fact.pulled_at
