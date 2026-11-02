SELECT answer_state FROM mart.ai_prompt_results_v2
WHERE engine = 'ai-overview' AND (answer_state IS NULL OR answer_state NOT IN ('answered', 'none shown'))
UNION ALL
SELECT answer_state FROM mart.peec_prompt_results_v1
WHERE engine = 'ai-overview' AND (answer_state IS NULL OR answer_state NOT IN ('answered', 'none shown'))
