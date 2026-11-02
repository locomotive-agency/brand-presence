WITH linked AS (
 SELECT answer.observation_date, answer.client_brand_id, answer.country_code,
 answer.location_id, answer.prompt_id, answer.engine, answer.period, link,
 answer.period_start, answer.pulled_at, answer.mapping, answer.answer_key, answer.request_key
 FROM stg.peec_answers AS answer
 CROSS JOIN UNNEST(answer.cited_links) AS link
 WHERE answer.citation_known
)
SELECT
  DATE_TRUNC(answer.observation_date, MONTH) AS month,
  answer.client_brand_id, answer.country_code, answer.location_id, answer.prompt_id,
  answer.engine, answer.period, answer.link, answer.period_start,
  answer.pulled_at AS source_pulled_at,
  answer.mapping, answer.answer_key, answer.request_key, answer.observation_date
FROM linked AS answer
JOIN stg.peec_pulls AS pull
  ON pull.request_key = answer.request_key AND pull.period = answer.period
  AND pull.pulled_at = answer.pulled_at
WHERE pull.complete_snapshot
