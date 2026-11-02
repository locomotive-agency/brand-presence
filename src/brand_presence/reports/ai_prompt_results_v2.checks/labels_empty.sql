-- No label is set before the stage 2 classification fills them: every row whose
-- recommendation or sentiment is set is returned.
SELECT
  result.run_id, result.client_brand_id, result.country_code, result.prompt_id, result.engine,
  result.geo_id, result.brand, result.is_recommended, result.sentiment
FROM mart.ai_prompt_results_v2 AS result
WHERE result.is_recommended IS NOT NULL
  OR result.sentiment IS NOT NULL
