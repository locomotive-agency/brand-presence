SELECT
  fact.month, fact.client_brand_id, fact.web_property_id, fact.country_code,
  fact.geo_level, fact.location_id, fact.channel_group
FROM core.fct_ga4_export_v1 AS fact
LEFT JOIN ref.current_locations AS place_row
  ON place_row.location_id = fact.location_id
WHERE fact.pulled_at = @pulled_at
  AND (
    fact.geo_level NOT IN ('national', 'state', 'city')
    OR (fact.geo_level = 'national'
      AND COALESCE(place_row.kind, '') != 'country'
      AND NOT (fact.location_id = 'unknown' AND fact.country_code = 'unknown'))
    OR (fact.geo_level = 'state' AND COALESCE(place_row.kind, 'region') != 'region')
    OR (fact.geo_level = 'city' AND COALESCE(place_row.kind, 'city') != 'city')
  )
