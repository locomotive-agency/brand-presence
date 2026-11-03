SELECT
  fact.month, fact.client_brand_id, fact.web_property_id, fact.page_path
FROM core.fct_ga4_export_pages_v1 AS fact
WHERE fact.pulled_at = @pulled_at
  AND fact.month != DATE_TRUNC(fact.month, MONTH)
