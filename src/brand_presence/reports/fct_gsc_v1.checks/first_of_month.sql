-- Every month of the build's rows is the first day of its month.
SELECT
  fact.month, fact.client_brand_id, fact.web_property_id, fact.country_code,
  fact.search_type, fact.row_kind, fact.query
FROM core.fct_gsc_v1 AS fact
WHERE fact.pulled_at = @pulled_at
  AND fact.month != DATE_TRUNC(fact.month, MONTH)
