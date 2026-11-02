-- Every month of the build's rows is the first day of its month.
SELECT fact.month, fact.client_brand_id, fact.match_id
FROM core.fct_mentions_v1 AS fact
WHERE fact.pulled_at = @pulled_at
  AND fact.month != DATE_TRUNC(fact.month, MONTH)
