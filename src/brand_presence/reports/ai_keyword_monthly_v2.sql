-- AI share of voice by keyword: one row per month, client brand, country, engine, place,
-- keyword and brand of the answers' sets, made from ai_prompt_results_v2. Each answer to the
-- keyword's prompts in the month counts once on each brand's row: whether it names the brand,
-- names it first, and cites the client brand's domains. An answer whose mark for the brand is
-- not known, one with no text, is counted in prompts_no_text and in no other count: never as
-- an answer that does not name the brand. Answers from January
-- 2024.
--
-- The keyword is the prompt's, from today's prompts tab, as ai_prompt_results_v1 shows it.
-- The answers to prompts that name no keyword, or that today's set no longer holds, are
-- counted under the keyword (none). gap_count is on the client brand's rows alone: the answers
-- that name a competitor of the set and not the client brand. The counts made from labels are
-- null until stage 2 fills them. A brand's mean rank where mentioned is mention_rank_total
-- over prompts_mentioned, a field of the dashboard. The sum of ranks is over one brand's
-- answers alone, never over brands. Where an answer of the row names the brand and has no
-- rank, as the source stored answers before it kept where a brand is first named,
-- first_mention_count and mention_rank_total are null: a mean over prompts_mentioned would
-- be wrong.
WITH results AS (
  SELECT
    result.run_id,
    result.month,
    result.client_brand_id,
    result.country_code,
    result.prompt_id,
    result.engine,
    result.geo_id,
    COALESCE(result.keyword, '(none)') AS keyword,
    result.keyword_group,
    result.intent,
    result.brand,
    result.brand_mentioned,
    result.mention_rank,
    result.client_domain_cited,
    result.prompt_set_version
  FROM mart.ai_prompt_results_v2 AS result
  WHERE result.month >= DATE '2024-01-01'
),
answers AS (
  SELECT
    answer.run_id,
    answer.client_brand_id,
    answer.country_code,
    answer.prompt_id,
    answer.engine,
    answer.geo_id,
    LOGICAL_OR(answer.brand = answer.client_brand_id AND answer.brand_mentioned)
      AS client_named,
    LOGICAL_OR(answer.brand != answer.client_brand_id AND answer.brand_mentioned)
      AS competitor_named
  FROM results AS answer
  GROUP BY answer.run_id, answer.client_brand_id, answer.country_code, answer.prompt_id,
    answer.engine, answer.geo_id
),
counted AS (
  SELECT
    results.month,
    results.client_brand_id,
    results.country_code,
    results.engine,
    results.geo_id,
    results.keyword,
    results.brand,
    MAX(results.keyword_group) AS keyword_group,
    MAX(results.intent) AS intent,
    SUM(IF(results.brand_mentioned IS NULL, 0, 1)) AS prompts_run,
    SUM(IF(results.brand_mentioned IS NULL, 1, 0)) AS prompts_no_text,
    SUM(IF(results.brand_mentioned, 1, 0)) AS prompts_mentioned,
    SUM(IF(results.mention_rank = 1, 1, 0)) AS first_mentions,
    COALESCE(SUM(results.mention_rank), 0) AS rank_total,
    SUM(IF(results.brand_mentioned AND results.mention_rank IS NULL, 1, 0)) AS named_unranked,
    SUM(IF(results.brand_mentioned IS NOT NULL AND results.client_domain_cited, 1, 0))
      AS client_cited_count,
    SUM(IF(answers.competitor_named AND NOT answers.client_named, 1, 0)) AS gaps,
    MAX(results.prompt_set_version) AS prompt_set_version
  FROM results
  JOIN answers
    ON answers.run_id = results.run_id
    AND answers.client_brand_id = results.client_brand_id
    AND answers.country_code = results.country_code
    AND answers.prompt_id = results.prompt_id
    AND answers.engine = results.engine
    AND answers.geo_id = results.geo_id
  GROUP BY results.month, results.client_brand_id, results.country_code, results.engine,
    results.geo_id, results.keyword, results.brand
)
SELECT
  counted.month,
  counted.client_brand_id,
  counted.country_code,
  counted.engine,
  counted.geo_id,
  counted.keyword,
  counted.brand,
  counted.keyword_group,
  counted.intent,
  counted.prompts_run,
  counted.prompts_no_text,
  counted.prompts_mentioned,
  SAFE_DIVIDE(counted.prompts_mentioned, counted.prompts_run) AS share_of_voice,
  IF(counted.named_unranked > 0, NULL, counted.first_mentions) AS first_mention_count,
  IF(counted.named_unranked > 0, NULL, counted.rank_total) AS mention_rank_total,
  CAST(NULL AS INT64) AS recommended_count,
  CAST(NULL AS INT64) AS positive_count,
  CAST(NULL AS INT64) AS neutral_count,
  CAST(NULL AS INT64) AS negative_count,
  counted.client_cited_count,
  IF(counted.brand = counted.client_brand_id, counted.gaps, NULL) AS gap_count,
  counted.prompt_set_version
FROM counted
