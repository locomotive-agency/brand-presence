SELECT
  engagement.month, engagement.client_brand_id, engagement.country_code,
  engagement.web_property_id, engagement.geo_id, engagement.channel_group,
  engagement.ga4_route
FROM mart.engagement_monthly_v1 AS engagement
JOIN (
  SELECT
    property_routes.web_property_id
  FROM mart.engagement_monthly_v1 AS property_routes
  GROUP BY property_routes.web_property_id
  HAVING MIN(property_routes.ga4_route) != MAX(property_routes.ga4_route)
) AS mixed
  ON mixed.web_property_id = engagement.web_property_id
