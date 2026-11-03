WITH newest AS (
  SELECT
    period_row.client_brand_id,
    period_row.web_property_id,
    period_row.month,
    MAX(period_row.period_start) AS period_start
  FROM stg.ga4_export_engagement AS period_row
  GROUP BY period_row.client_brand_id, period_row.web_property_id, period_row.month
)
SELECT
  export_row.month,
  export_row.client_brand_id,
  export_row.web_property_id,
  export_row.country_code,
  export_row.geo_level,
  export_row.location_id,
  export_row.channel_group,
  export_row.through_date,
  export_row.period_start,
  export_row.sessions,
  export_row.page_views,
  export_row.engagement_seconds,
  export_row.active_users,
  export_row.total_users,
  export_row.new_users,
  export_row.returning_users
FROM stg.ga4_export_engagement AS export_row
JOIN newest
  ON newest.client_brand_id = export_row.client_brand_id
  AND newest.web_property_id = export_row.web_property_id
  AND newest.month = export_row.month
  AND newest.period_start = export_row.period_start
LEFT JOIN ref.current_locations AS place_row
  ON place_row.location_id = export_row.location_id
