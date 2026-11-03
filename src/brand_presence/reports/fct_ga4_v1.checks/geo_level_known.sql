SELECT
  fact.month, fact.client_brand_id, fact.web_property_id, fact.country_code,
  fact.geo_level, fact.location_id, fact.channel_group
FROM core.fct_ga4_v1 AS fact
LEFT JOIN ref.current_locations AS location
  ON location.location_id = fact.location_id
WHERE fact.pulled_at = @pulled_at
  AND (
    fact.geo_level NOT IN ('country', 'city')
    OR (fact.geo_level = 'country' AND COALESCE(location.kind, '') != 'country')
  )
