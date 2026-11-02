-- Each AI answer and each brand of its set, for each client brand, country, location, prompt,
-- engine and pull period, as the ai-answers source stored them: the answer's state, whether
-- the brand is named and cited, where it is first named, its rank among the set's brands by
-- that place, and a snippet of the answer around it.
--
-- An answer asked again in the same period was replaced in the clean tables by the newer
-- answer, its brands with it, so each period holds one answer for each prompt, engine and
-- location, and every period is shown. The answer's own time is source_pulled_at, so a reader
-- can tell a row of the latest answer from one the fact kept from an earlier one. The month is
-- that of the period's first day.
--
-- The brands of an answer are those the source stored for it, of the benchmark version the
-- request was planned from: the client brand and its competitors. An answer with no text, an
-- engine that showed none, has no brand in the source; it has one row here, with the subject
-- brand (none), so the answer is still counted. Nothing of today's configuration is read.
--
-- answer_snippet is cut from the stored answer around the brand's first mention: from 100
-- characters before it, 300 characters at most, each run of white space made one space. It is
-- null where the brand is not named, and on a row the source wrote before it stored where a
-- brand is first named.
SELECT
  DATE_TRUNC(answer.period_start, MONTH) AS month,
  answer.client_brand_id,
  answer.country_code,
  answer.location_id,
  answer.prompt_id,
  answer.engine,
  answer.period,
  COALESCE(brand_row.subject_brand_id, '(none)') AS subject_brand_id,
  answer.period_start,
  answer.pulled_at AS source_pulled_at,
  answer.benchmark_version,
  answer.state,
  brand_row.named,
  brand_row.cited,
  brand_row.first_mention_at,
  brand_row.mention_rank,
  CASE
    WHEN brand_row.first_mention_at IS NOT NULL THEN TRIM(REGEXP_REPLACE(
      SUBSTR(answer.answer_text, GREATEST(1, brand_row.first_mention_at - 99), 300),
      '\\s+', ' '))
  END AS answer_snippet
FROM stg.ai_answers AS answer
LEFT JOIN stg.ai_answer_brands AS brand_row
  ON brand_row.client_brand_id = answer.client_brand_id
  AND brand_row.country_code = answer.country_code
  AND brand_row.location_id = answer.location_id
  AND brand_row.prompt_id = answer.prompt_id
  AND brand_row.engine = answer.engine
  AND brand_row.period = answer.period
