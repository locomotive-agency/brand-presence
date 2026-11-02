-- The client brand row represents each answer once, including each distinct Peec chat.
WITH answers AS (
  SELECT c.run_date, 'cloro' AS source, COALESCE(c.keyword_group, 'unknown') AS keyword_group, c.answer_state
  FROM mart.ai_prompt_results_v2 AS c
  WHERE c.brand = c.client_brand_id AND c.engine = 'ai-overview'
  UNION ALL
  SELECT p.run_date, 'peec' AS source, COALESCE(p.keyword_group, 'unknown') AS keyword_group, p.answer_state
  FROM mart.peec_prompt_results_v1 AS p
  WHERE p.brand = p.client_brand_id AND p.engine = 'ai-overview'
)
SELECT DATE_TRUNC(run_date, ISOWEEK) AS week_start, source, keyword_group,
  COUNT(*) AS queries_total,
  SUM(IF(answer_state = 'answered', 1, 0)) AS queries_with_aio
FROM answers
GROUP BY week_start, source, keyword_group
