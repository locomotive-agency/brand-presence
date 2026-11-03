WITH queries AS (
  SELECT
    q.client_brand_id,
    q.web_property_id,
    q.country_code,
    q.search_type,
    q.query,
    q.date,
    DATE_TRUNC(q.date, MONTH) AS month,
    q.term_type,
    q.subject_brand,
    q.clicks,
    q.impressions,
    IF(q.impressions = 0, CAST(q.position_sum AS NUMERIC),
      CAST(q.position * q.impressions AS NUMERIC)) AS position_weighted
  FROM stg.search_console_export_queries AS q
  QUALIFY DENSE_RANK() OVER (
    PARTITION BY q.client_brand_id, q.web_property_id, q.country_code,
      q.search_type, q.date
    ORDER BY q.pulled_at DESC, q.period DESC
  ) = 1
),
totals AS (
  SELECT
    total.client_brand_id,
    total.web_property_id,
    total.country_code,
    total.search_type,
    total.date,
    DATE_TRUNC(total.date, MONTH) AS month,
    total.clicks,
    total.impressions,
    total.anonymized_clicks,
    total.anonymized_impressions,
    IF(total.impressions = 0, CAST(total.position_sum AS NUMERIC),
      CAST(total.position * total.impressions AS NUMERIC)) AS position_weighted
  FROM stg.search_console_export_totals AS total
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY total.client_brand_id, total.web_property_id, total.country_code,
      total.search_type, total.date
    ORDER BY total.pulled_at DESC, total.period DESC
  ) = 1
),
covered AS (
  SELECT web_property_id, search_type, month, COUNT(DISTINCT date) AS days_covered
  FROM totals
  GROUP BY web_property_id, search_type, month
),
labelled AS (
  SELECT
    client_brand_id, web_property_id, country_code, search_type, query, month, term_type,
    subject_brand
  FROM queries
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY client_brand_id, web_property_id, country_code, search_type, query, month
    ORDER BY date DESC
  ) = 1
),
query_rows AS (
  SELECT
    month,
    client_brand_id,
    web_property_id,
    country_code,
    search_type,
    query,
    SUM(clicks) AS clicks,
    SUM(impressions) AS impressions,
    SUM(position_weighted) AS position_weighted
  FROM queries
  GROUP BY month, client_brand_id, web_property_id, country_code, search_type, query
),
total_rows AS (
  SELECT
    month,
    client_brand_id,
    web_property_id,
    country_code,
    search_type,
    SUM(clicks) AS clicks,
    SUM(impressions) AS impressions,
    SUM(position_weighted) AS position_weighted,
    SUM(anonymized_clicks) AS anonymized_clicks,
    SUM(anonymized_impressions) AS anonymized_impressions
  FROM totals
  GROUP BY month, client_brand_id, web_property_id, country_code, search_type
)
SELECT
  query_rows.month,
  query_rows.client_brand_id,
  query_rows.web_property_id,
  query_rows.country_code,
  query_rows.search_type,
  'query' AS row_kind,
  query_rows.query,
  labelled.term_type,
  labelled.subject_brand AS subject_brand_id,
  query_rows.clicks,
  query_rows.impressions,
  query_rows.position_weighted,
  covered.days_covered,
  CAST(NULL AS INT64) AS anonymized_clicks,
  CAST(NULL AS INT64) AS anonymized_impressions
FROM query_rows
JOIN labelled
  ON labelled.client_brand_id = query_rows.client_brand_id
  AND labelled.web_property_id = query_rows.web_property_id
  AND labelled.country_code = query_rows.country_code
  AND labelled.search_type = query_rows.search_type
  AND labelled.query = query_rows.query
  AND labelled.month = query_rows.month
LEFT JOIN covered
  ON covered.web_property_id = query_rows.web_property_id
  AND covered.search_type = query_rows.search_type
  AND covered.month = query_rows.month
UNION ALL
SELECT
  total_rows.month,
  total_rows.client_brand_id,
  total_rows.web_property_id,
  total_rows.country_code,
  total_rows.search_type,
  'total' AS row_kind,
  '(all)' AS query,
  '(all)' AS term_type,
  CAST(NULL AS STRING) AS subject_brand_id,
  total_rows.clicks,
  total_rows.impressions,
  total_rows.position_weighted,
  covered.days_covered,
  total_rows.anonymized_clicks,
  total_rows.anonymized_impressions
FROM total_rows
LEFT JOIN covered
  ON covered.web_property_id = total_rows.web_property_id
  AND covered.search_type = total_rows.search_type
  AND covered.month = total_rows.month
