-- Every month of the build's rows is the first day of its month.
SELECT
  fact.month, fact.client_brand_id, fact.country_code, fact.location_id, fact.prompt_id,
  fact.engine, fact.period, fact.link
FROM core.fct_ai_cited_links_v1 AS fact
WHERE fact.pulled_at = @pulled_at
  AND fact.month != DATE_TRUNC(fact.month, MONTH)
