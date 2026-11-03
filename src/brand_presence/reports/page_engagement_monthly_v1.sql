WITH pull_times AS (
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
page_latest AS (
  SELECT
    page_key.month,
    page_key.client_brand_id,
    page_key.web_property_id,
    page_key.page_path,
    MAX(page_key.pulled_at) AS pulled_at
  FROM core.fct_ga4_export_pages_v1 AS page_key
  GROUP BY page_key.month, page_key.client_brand_id, page_key.web_property_id,
    page_key.page_path
),
page_periods AS (
  SELECT
    page_period.month,
    page_period.client_brand_id,
    page_period.web_property_id,
    MAX(page_period.period_start) AS period_start
  FROM core.fct_ga4_export_pages_v1 AS page_period
  GROUP BY page_period.month, page_period.client_brand_id, page_period.web_property_id
)
SELECT
  page_row.month,
  page_row.client_brand_id,
  page_row.web_property_id,
  page_row.page_path,
  named_page.page_group,
  routes.ga4_route,
  page_row.page_views,
  page_row.users,
  page_row.engagement_seconds AS engagement_duration_sec,
  SAFE_DIVIDE(CAST(page_row.engagement_seconds AS FLOAT64), page_row.users)
    AS avg_engagement_sec,
  page_row.month < DATE_TRUNC(CURRENT_DATE(@client_time_zone), MONTH)
    AND page_row.through_date
      = DATE_SUB(DATE_ADD(page_row.month, INTERVAL 1 MONTH), INTERVAL 1 DAY)
    AS is_complete_month,
  page_row.pulled_at
FROM core.fct_ga4_export_pages_v1 AS page_row
JOIN page_latest
  ON page_latest.month = page_row.month
  AND page_latest.client_brand_id = page_row.client_brand_id
  AND page_latest.web_property_id = page_row.web_property_id
  AND page_latest.page_path = page_row.page_path
  AND page_latest.pulled_at = page_row.pulled_at
JOIN page_periods
  ON page_periods.month = page_row.month
  AND page_periods.client_brand_id = page_row.client_brand_id
  AND page_periods.web_property_id = page_row.web_property_id
  AND page_periods.period_start = page_row.period_start
JOIN routes
  ON routes.web_property_id = page_row.web_property_id
  AND routes.client_brand_id = page_row.client_brand_id
JOIN ref.current_pages AS named_page
  ON named_page.page_path = page_row.page_path
WHERE routes.ga4_route = 'export'
  AND page_row.month >= DATE '2024-01-01'
