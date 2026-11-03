WITH newest AS (
  SELECT
    period_row.client_brand_id,
    period_row.web_property_id,
    period_row.month,
    MAX(period_row.period_start) AS period_start
  FROM stg.ga4_export_pages AS period_row
  GROUP BY period_row.client_brand_id, period_row.web_property_id, period_row.month
)
SELECT
  page_row.month,
  page_row.client_brand_id,
  page_row.web_property_id,
  page_row.page_path,
  page_row.through_date,
  page_row.period_start,
  page_row.page_views,
  page_row.users,
  page_row.engagement_seconds
FROM stg.ga4_export_pages AS page_row
JOIN newest
  ON newest.client_brand_id = page_row.client_brand_id
  AND newest.web_property_id = page_row.web_property_id
  AND newest.month = page_row.month
  AND newest.period_start = page_row.period_start
