SELECT
  DATE_TRUNC(answer.observation_date, MONTH) AS month,
  answer.client_brand_id, answer.country_code, answer.location_id, answer.prompt_id,
  answer.engine, answer.period,
  COALESCE(brand_row.subject_brand_id, '(none)') AS subject_brand_id, answer.period_start,
  answer.pulled_at AS source_pulled_at, answer.benchmark_version, answer.state,
  brand_row.named, brand_row.cited, brand_row.first_mention_at, brand_row.mention_rank,
  CASE WHEN brand_row.first_mention_at IS NOT NULL THEN TRIM(REGEXP_REPLACE(
    SUBSTR(answer.answer_text, GREATEST(1, brand_row.first_mention_at - 99), 300),
    '\\s+', ' ')) END AS answer_snippet,
  answer.mapping, answer.answer_key, answer.request_key, answer.observation_date,
  answer.citation_known, answer.prompt_version, brand_row.vendor_mentioned, brand_row.vendor_position
FROM stg.peec_answers AS answer
LEFT JOIN stg.peec_answer_brands AS brand_row
  ON brand_row.mapping = answer.mapping AND brand_row.answer_key = answer.answer_key
  AND brand_row.period = answer.period AND brand_row.request_key = answer.request_key
  AND brand_row.pulled_at = answer.pulled_at
JOIN stg.peec_pulls AS pull
  ON pull.request_key = answer.request_key AND pull.period = answer.period
  AND pull.pulled_at = answer.pulled_at
WHERE pull.complete_snapshot
