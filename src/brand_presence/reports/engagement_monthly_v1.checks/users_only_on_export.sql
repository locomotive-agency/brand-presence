SELECT
  engagement.month, engagement.client_brand_id, engagement.country_code,
  engagement.web_property_id, engagement.geo_id, engagement.channel_group,
  engagement.ga4_route, engagement.active_users, engagement.total_users,
  engagement.returning_users
FROM mart.engagement_monthly_v1 AS engagement
WHERE engagement.ga4_route = 'data_api'
  AND (engagement.active_users IS NOT NULL
    OR engagement.total_users IS NOT NULL
    OR engagement.returning_users IS NOT NULL)
