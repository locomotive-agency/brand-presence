-- Every link an AI answer cites, for each client brand, country, location, prompt, engine and
-- pull period, as the ai-answers source stored it: one row per answer and link.
--
-- An answer asked again in the same period was replaced in the clean tables by the newer
-- answer, its links with it. The answer's own time is source_pulled_at, so a reader can tell a
-- link of the latest answer from one the fact kept from an earlier one. The month is that of
-- the period's first day. An answer that cites nothing has no row. A row the source wrote
-- before it kept full links holds none, so its answer has no row either until its period is
-- written again from raw. Nothing of today's configuration is read.
SELECT
  DATE_TRUNC(answer.period_start, MONTH) AS month,
  answer.client_brand_id,
  answer.country_code,
  answer.location_id,
  answer.prompt_id,
  answer.engine,
  answer.period,
  cited_link AS link,
  answer.period_start,
  answer.pulled_at AS source_pulled_at
FROM stg.ai_answers AS answer
CROSS JOIN UNNEST(answer.cited_links) AS cited_link
