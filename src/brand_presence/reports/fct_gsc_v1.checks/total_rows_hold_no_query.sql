-- A total row of the build holds the query and the term type (all), so no reader takes it
-- for a named query.
SELECT
  fact.month, fact.client_brand_id, fact.web_property_id, fact.country_code,
  fact.search_type, fact.query, fact.term_type
FROM core.fct_gsc_v1 AS fact
WHERE fact.pulled_at = @pulled_at
  AND fact.row_kind = 'total'
  AND (fact.query != '(all)' OR COALESCE(fact.term_type, '') != '(all)')
