WITH places AS (
  SELECT
    place_row.location_id,
    place_row.country_code,
    CASE
      WHEN place_row.kind = 'country' THEN 'national'
      WHEN place_row.kind = 'region' THEN 'state'
      ELSE 'city'
    END AS geo_level
  FROM ref.current_locations AS place_row
  WHERE place_row.kind IN ('country', 'region', 'city')
    AND STRPOS(CONCAT(',', ARRAY_TO_STRING(place_row.sources, ','), ','), ',ga4,') > 0
),
pull_times AS (
  SELECT
    pull_row.web_property_id,
    MAX(IF(pull_row.report IN ('export_engagement', 'export_pages'), pull_row.pulled_at,
      NULL)) AS export_pulled_at,
    MAX(IF(pull_row.report IN ('export_engagement', 'export_pages'), NULL,
      pull_row.pulled_at)) AS api_pulled_at
  FROM stg.ga4_pulls AS pull_row
  GROUP BY pull_row.web_property_id
),
routes AS (
  SELECT
    property_row.web_property_id,
    property_row.client_brand_id,
    IF(pull_times.export_pulled_at IS NOT NULL
      AND (pull_times.api_pulled_at IS NULL
        OR pull_times.export_pulled_at >= pull_times.api_pulled_at),
      'export', 'data_api') AS ga4_route
  FROM ref.current_web_properties AS property_row
  JOIN pull_times
    ON pull_times.web_property_id = property_row.web_property_id
  WHERE property_row.source = 'ga4'
),
export_latest AS (
  SELECT
    export_key.month,
    export_key.client_brand_id,
    export_key.web_property_id,
    export_key.country_code,
    export_key.geo_level,
    export_key.location_id,
    export_key.channel_group,
    MAX(export_key.pulled_at) AS pulled_at
  FROM core.fct_ga4_export_v1 AS export_key
  GROUP BY export_key.month, export_key.client_brand_id, export_key.web_property_id,
    export_key.country_code, export_key.geo_level, export_key.location_id,
    export_key.channel_group
),
export_periods AS (
  SELECT
    export_period.month,
    export_period.client_brand_id,
    export_period.web_property_id,
    MAX(export_period.period_start) AS period_start
  FROM core.fct_ga4_export_v1 AS export_period
  GROUP BY export_period.month, export_period.client_brand_id, export_period.web_property_id
),
export_rows AS (
  SELECT
    export_row.month,
    export_row.client_brand_id,
    export_row.country_code,
    export_row.web_property_id,
    export_row.location_id AS geo_id,
    places.geo_level,
    export_row.channel_group,
    'export' AS ga4_route,
    export_row.sessions,
    export_row.page_views,
    export_row.engagement_seconds AS engagement_duration_sec,
    export_row.active_users,
    export_row.total_users,
    export_row.returning_users,
    FALSE AS sampled,
    export_row.month < DATE_TRUNC(CURRENT_DATE(@client_time_zone), MONTH)
      AND export_row.through_date
        = DATE_SUB(DATE_ADD(export_row.month, INTERVAL 1 MONTH), INTERVAL 1 DAY)
      AS is_complete_month,
    export_row.pulled_at
  FROM core.fct_ga4_export_v1 AS export_row
  JOIN export_latest
    ON export_latest.month = export_row.month
    AND export_latest.client_brand_id = export_row.client_brand_id
    AND export_latest.web_property_id = export_row.web_property_id
    AND export_latest.country_code = export_row.country_code
    AND export_latest.geo_level = export_row.geo_level
    AND export_latest.location_id = export_row.location_id
    AND export_latest.channel_group = export_row.channel_group
    AND export_latest.pulled_at = export_row.pulled_at
  JOIN export_periods
    ON export_periods.month = export_row.month
    AND export_periods.client_brand_id = export_row.client_brand_id
    AND export_periods.web_property_id = export_row.web_property_id
    AND export_periods.period_start = export_row.period_start
  JOIN routes
    ON routes.web_property_id = export_row.web_property_id
    AND routes.client_brand_id = export_row.client_brand_id
  JOIN places
    ON places.location_id = export_row.location_id
    AND places.country_code = export_row.country_code
    AND places.geo_level = export_row.geo_level
  WHERE routes.ga4_route = 'export'
),
api_latest AS (
  SELECT
    api_key.month,
    api_key.client_brand_id,
    api_key.web_property_id,
    api_key.country_code,
    api_key.geo_level,
    api_key.location_id,
    api_key.channel_group,
    MAX(api_key.pulled_at) AS pulled_at
  FROM core.fct_ga4_v1 AS api_key
  GROUP BY api_key.month, api_key.client_brand_id, api_key.web_property_id,
    api_key.country_code, api_key.geo_level, api_key.location_id, api_key.channel_group
),
api_levels AS (
  SELECT
    api_row.month,
    api_row.client_brand_id,
    api_row.country_code,
    api_row.web_property_id,
    api_row.location_id,
    IF(api_row.geo_level = 'country', 'national', api_row.geo_level) AS geo_level,
    api_row.channel_group,
    api_row.sessions,
    api_row.page_views,
    api_row.engagement_seconds,
    api_row.sampled,
    api_row.days_covered,
    api_row.pulled_at
  FROM core.fct_ga4_v1 AS api_row
  JOIN api_latest
    ON api_latest.month = api_row.month
    AND api_latest.client_brand_id = api_row.client_brand_id
    AND api_latest.web_property_id = api_row.web_property_id
    AND api_latest.country_code = api_row.country_code
    AND api_latest.geo_level = api_row.geo_level
    AND api_latest.location_id = api_row.location_id
    AND api_latest.channel_group = api_row.channel_group
    AND api_latest.pulled_at = api_row.pulled_at
  JOIN routes
    ON routes.web_property_id = api_row.web_property_id
    AND routes.client_brand_id = api_row.client_brand_id
  WHERE routes.ga4_route = 'data_api'
),
api_places AS (
  SELECT
    api_level.month,
    api_level.client_brand_id,
    api_level.country_code,
    api_level.web_property_id,
    api_level.location_id AS geo_id,
    places.geo_level,
    api_level.channel_group,
    api_level.sessions,
    api_level.page_views,
    api_level.engagement_seconds,
    api_level.sampled,
    api_level.days_covered,
    api_level.pulled_at
  FROM api_levels AS api_level
  JOIN places
    ON places.location_id = api_level.location_id
    AND places.country_code = api_level.country_code
    AND places.geo_level = api_level.geo_level
),
api_rows AS (
  SELECT
    channel_side.month,
    channel_side.client_brand_id,
    channel_side.country_code,
    channel_side.web_property_id,
    channel_side.geo_id,
    channel_side.geo_level,
    channel_side.channel_group,
    channel_side.sessions,
    channel_side.page_views,
    channel_side.engagement_seconds,
    channel_side.sampled,
    channel_side.days_covered,
    channel_side.pulled_at
  FROM api_places AS channel_side
  UNION ALL
  SELECT
    total_side.month,
    total_side.client_brand_id,
    total_side.country_code,
    total_side.web_property_id,
    total_side.geo_id,
    total_side.geo_level,
    '(all)' AS channel_group,
    SUM(total_side.sessions) AS sessions,
    SUM(total_side.page_views) AS page_views,
    SUM(total_side.engagement_seconds) AS engagement_seconds,
    LOGICAL_OR(total_side.sampled) AS sampled,
    MIN(total_side.days_covered) AS days_covered,
    MAX(total_side.pulled_at) AS pulled_at
  FROM api_places AS total_side
  WHERE total_side.geo_level = 'national'
  GROUP BY total_side.month, total_side.client_brand_id, total_side.country_code,
    total_side.web_property_id, total_side.geo_id, total_side.geo_level
),
both_routes AS (
  SELECT
    export_side.month,
    export_side.client_brand_id,
    export_side.country_code,
    export_side.web_property_id,
    export_side.geo_id,
    export_side.geo_level,
    export_side.channel_group,
    export_side.ga4_route,
    export_side.sessions,
    export_side.page_views,
    export_side.engagement_duration_sec,
    export_side.active_users,
    export_side.total_users,
    export_side.returning_users,
    export_side.sampled,
    export_side.is_complete_month,
    export_side.pulled_at
  FROM export_rows AS export_side
  UNION ALL
  SELECT
    api_side.month,
    api_side.client_brand_id,
    api_side.country_code,
    api_side.web_property_id,
    api_side.geo_id,
    api_side.geo_level,
    api_side.channel_group,
    'data_api' AS ga4_route,
    CAST(api_side.sessions AS INT64) AS sessions,
    CAST(api_side.page_views AS INT64) AS page_views,
    api_side.engagement_seconds AS engagement_duration_sec,
    CAST(NULL AS INT64) AS active_users,
    CAST(NULL AS INT64) AS total_users,
    CAST(NULL AS INT64) AS returning_users,
    api_side.sampled,
    api_side.month < DATE_TRUNC(CURRENT_DATE(@client_time_zone), MONTH)
      AND DATE_ADD(api_side.month, INTERVAL api_side.days_covered DAY)
        = DATE_ADD(api_side.month, INTERVAL 1 MONTH)
      AS is_complete_month,
    api_side.pulled_at
  FROM api_rows AS api_side
)
SELECT
  both_routes.month,
  both_routes.client_brand_id,
  both_routes.country_code,
  both_routes.web_property_id,
  both_routes.geo_id,
  both_routes.geo_level,
  both_routes.channel_group,
  both_routes.ga4_route,
  both_routes.sessions,
  both_routes.page_views,
  SAFE_DIVIDE(both_routes.page_views, both_routes.sessions) AS pages_per_session,
  both_routes.engagement_duration_sec,
  both_routes.active_users,
  both_routes.total_users,
  both_routes.returning_users,
  SAFE_DIVIDE(CAST(both_routes.engagement_duration_sec AS FLOAT64), both_routes.total_users)
    AS time_on_site_sec,
  SAFE_DIVIDE(both_routes.returning_users, both_routes.total_users) AS return_visit_rate,
  both_routes.sampled,
  both_routes.is_complete_month,
  both_routes.pulled_at
FROM both_routes
WHERE both_routes.month >= DATE '2024-01-01'
