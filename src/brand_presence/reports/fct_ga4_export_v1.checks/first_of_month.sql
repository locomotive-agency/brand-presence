SELECT
  fact.month, fact.client_brand_id, fact.web_property_id, fact.country_code,
  fact.geo_level, fact.location_id, fact.channel_group
FROM core.fct_ga4_export_v1 AS fact
WHERE fact.pulled_at = @pulled_at
  AND fact.month != DATE_TRUNC(fact.month, MONTH)
