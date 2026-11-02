-- Each AI prompt result: one row per pull period, client brand, country, prompt, engine,
-- place and brand of the answer's set, mentioned or not, so gaps can be queried. A row says
-- whether the answer names the brand, the brand's rank among the set's brands by where each is
-- first named, whether the answer cites the client brand's domains, every link it cites, and a
-- snippet around the brand's first mention.
--
-- The facts are read at their latest pull per key, and each answer at its latest answer: the
-- rows whose source_pulled_at is the answer's latest, so a brand or a link that a newer answer
-- of the same period no longer holds is left out. run_id is the pull period; run_date the day,
-- in the client's time zone, the answer was received, and month its month.
--
-- The brands of an answer are those of the benchmark version the request was planned from,
-- never today's (rule 1): the competitors of that version in ref.benchmark_competitors, and
-- the client brand itself, as today's brands tab names it. brand_set_version and
-- prompt_set_version are that version. An answer with no text has a row for each brand of
-- its set, and on it brand_mentioned and client_domain_cited are null: whether it names or
-- cites a brand is not known, and is never taken to be no. They are
-- the source's own marks, null where it holds none. client_domain_cited is the client brand's
-- own cited mark, the same on every brand row of an answer.
--
-- What is shown as of today (rule 3): the brand's name from today's brands tab; the prompt's
-- keyword, keyword group and intent from today's prompts tab, null where today's set no longer
-- holds the prompt or it names no keyword; and the place's level from today's locations tab.
-- Only the places today's locations tab lists for ai-answers show. is_recommended and
-- sentiment are null until stage 2 labels them.
WITH answer_latest AS (
  SELECT
    latest.month,
    latest.client_brand_id,
    latest.country_code,
    latest.location_id,
    latest.prompt_id,
    latest.engine,
    latest.period,
    latest.subject_brand_id,
    MAX(latest.pulled_at) AS pulled_at
  FROM core.fct_ai_answers_v1 AS latest
  GROUP BY latest.month, latest.client_brand_id, latest.country_code, latest.location_id,
    latest.prompt_id, latest.engine, latest.period, latest.subject_brand_id
),
answer_rows AS (
  SELECT
    fact.client_brand_id,
    fact.country_code,
    fact.location_id,
    fact.prompt_id,
    fact.engine,
    fact.period,
    fact.subject_brand_id,
    fact.source_pulled_at,
    fact.benchmark_version,
    fact.state,
    fact.named,
    fact.cited,
    fact.mention_rank,
    fact.answer_snippet,
    fact.pulled_at
  FROM core.fct_ai_answers_v1 AS fact
  JOIN answer_latest
    ON answer_latest.month = fact.month
    AND answer_latest.client_brand_id = fact.client_brand_id
    AND answer_latest.country_code = fact.country_code
    AND answer_latest.location_id = fact.location_id
    AND answer_latest.prompt_id = fact.prompt_id
    AND answer_latest.engine = fact.engine
    AND answer_latest.period = fact.period
    AND answer_latest.subject_brand_id = fact.subject_brand_id
    AND answer_latest.pulled_at = fact.pulled_at
),
received AS (
  SELECT
    answered.client_brand_id,
    answered.country_code,
    answered.location_id,
    answered.prompt_id,
    answered.engine,
    answered.period,
    MAX(answered.source_pulled_at) AS source_pulled_at
  FROM answer_rows AS answered
  GROUP BY answered.client_brand_id, answered.country_code, answered.location_id,
    answered.prompt_id, answered.engine, answered.period
),
shown AS (
  SELECT
    answer_rows.client_brand_id,
    answer_rows.country_code,
    answer_rows.location_id,
    answer_rows.prompt_id,
    answer_rows.engine,
    answer_rows.period,
    answer_rows.subject_brand_id,
    answer_rows.source_pulled_at,
    answer_rows.benchmark_version,
    answer_rows.state,
    answer_rows.named,
    answer_rows.cited,
    answer_rows.mention_rank,
    answer_rows.answer_snippet,
    answer_rows.pulled_at
  FROM answer_rows
  JOIN received
    ON received.client_brand_id = answer_rows.client_brand_id
    AND received.country_code = answer_rows.country_code
    AND received.location_id = answer_rows.location_id
    AND received.prompt_id = answer_rows.prompt_id
    AND received.engine = answer_rows.engine
    AND received.period = answer_rows.period
    AND received.source_pulled_at = answer_rows.source_pulled_at
),
answers AS (
  SELECT
    asked.client_brand_id,
    asked.country_code,
    asked.location_id,
    asked.prompt_id,
    asked.engine,
    asked.period,
    asked.source_pulled_at,
    MAX(asked.benchmark_version) AS benchmark_version,
    MAX(asked.state) AS state,
    MAX(asked.pulled_at) AS pulled_at
  FROM shown AS asked
  GROUP BY asked.client_brand_id, asked.country_code, asked.location_id, asked.prompt_id,
    asked.engine, asked.period, asked.source_pulled_at
),
members AS (
  SELECT
    with_competitor.client_brand_id,
    with_competitor.country_code,
    with_competitor.location_id,
    with_competitor.prompt_id,
    with_competitor.engine,
    with_competitor.period,
    competitor.brand_id AS brand
  FROM answers AS with_competitor
  JOIN ref.benchmark_competitors AS competitor
    ON competitor.client_brand_id = with_competitor.client_brand_id
    AND competitor.country_code = with_competitor.country_code
    AND competitor.benchmark_version = with_competitor.benchmark_version
  UNION ALL
  SELECT
    with_client.client_brand_id,
    with_client.country_code,
    with_client.location_id,
    with_client.prompt_id,
    with_client.engine,
    with_client.period,
    client.brand_id AS brand
  FROM answers AS with_client
  JOIN ref.current_brands AS client
    ON client.brand_id = with_client.client_brand_id
),
link_latest AS (
  SELECT
    newest.month,
    newest.client_brand_id,
    newest.country_code,
    newest.location_id,
    newest.prompt_id,
    newest.engine,
    newest.period,
    newest.link,
    MAX(newest.pulled_at) AS pulled_at
  FROM core.fct_ai_cited_links_v1 AS newest
  GROUP BY newest.month, newest.client_brand_id, newest.country_code, newest.location_id,
    newest.prompt_id, newest.engine, newest.period, newest.link
),
link_rows AS (
  SELECT
    cited_fact.client_brand_id,
    cited_fact.country_code,
    cited_fact.location_id,
    cited_fact.prompt_id,
    cited_fact.engine,
    cited_fact.period,
    cited_fact.link,
    cited_fact.source_pulled_at,
    cited_fact.pulled_at
  FROM core.fct_ai_cited_links_v1 AS cited_fact
  JOIN link_latest
    ON link_latest.month = cited_fact.month
    AND link_latest.client_brand_id = cited_fact.client_brand_id
    AND link_latest.country_code = cited_fact.country_code
    AND link_latest.location_id = cited_fact.location_id
    AND link_latest.prompt_id = cited_fact.prompt_id
    AND link_latest.engine = cited_fact.engine
    AND link_latest.period = cited_fact.period
    AND link_latest.link = cited_fact.link
    AND link_latest.pulled_at = cited_fact.pulled_at
),
links AS (
  SELECT
    link_rows.client_brand_id,
    link_rows.country_code,
    link_rows.location_id,
    link_rows.prompt_id,
    link_rows.engine,
    link_rows.period,
    ARRAY_AGG(link_rows.link ORDER BY link_rows.link) AS cited_urls,
    MAX(link_rows.pulled_at) AS pulled_at
  FROM link_rows
  JOIN answers AS linked
    ON linked.client_brand_id = link_rows.client_brand_id
    AND linked.country_code = link_rows.country_code
    AND linked.location_id = link_rows.location_id
    AND linked.prompt_id = link_rows.prompt_id
    AND linked.engine = link_rows.engine
    AND linked.period = link_rows.period
    AND linked.source_pulled_at = link_rows.source_pulled_at
  GROUP BY link_rows.client_brand_id, link_rows.country_code, link_rows.location_id,
    link_rows.prompt_id, link_rows.engine, link_rows.period
),
places AS (
  SELECT
    location.location_id,
    location.country_code,
    CASE
      WHEN location.kind = 'country' THEN 'national'
      WHEN location.kind = 'region' THEN 'state'
      ELSE 'city'
    END AS geo_level
  FROM ref.current_locations AS location
  WHERE location.kind IN ('country', 'region', 'city')
    AND STRPOS(CONCAT(',', ARRAY_TO_STRING(location.sources, ','), ','), ',ai-answers,') > 0
)
SELECT
  answers.period AS run_id,
  DATE(answers.source_pulled_at, @client_time_zone) AS run_date,
  DATE_TRUNC(DATE(answers.source_pulled_at, @client_time_zone), MONTH) AS month,
  answers.client_brand_id,
  answers.country_code,
  answers.prompt_id,
  prompt_tab.keyword,
  prompt_tab.keyword_group,
  prompt_tab.intent,
  answers.engine,
  answers.location_id AS geo_id,
  places.geo_level,
  members.brand,
  brand_names.name AS brand_name,
  answers.benchmark_version AS brand_set_version,
  answers.state AS answer_state,
  brand_named.named AS brand_mentioned,
  brand_named.mention_rank,
  CAST(NULL AS BOOL) AS is_recommended,
  CAST(NULL AS STRING) AS sentiment,
  own.cited AS client_domain_cited,
  COALESCE(links.cited_urls, []) AS cited_urls,
  brand_named.answer_snippet,
  answers.benchmark_version AS prompt_set_version,
  GREATEST(answers.pulled_at, COALESCE(links.pulled_at, answers.pulled_at)) AS pulled_at
FROM answers
JOIN places
  ON places.location_id = answers.location_id
  AND places.country_code = answers.country_code
JOIN members
  ON members.client_brand_id = answers.client_brand_id
  AND members.country_code = answers.country_code
  AND members.location_id = answers.location_id
  AND members.prompt_id = answers.prompt_id
  AND members.engine = answers.engine
  AND members.period = answers.period
LEFT JOIN shown AS brand_named
  ON brand_named.client_brand_id = answers.client_brand_id
  AND brand_named.country_code = answers.country_code
  AND brand_named.location_id = answers.location_id
  AND brand_named.prompt_id = answers.prompt_id
  AND brand_named.engine = answers.engine
  AND brand_named.period = answers.period
  AND brand_named.subject_brand_id = members.brand
LEFT JOIN shown AS own
  ON own.client_brand_id = answers.client_brand_id
  AND own.country_code = answers.country_code
  AND own.location_id = answers.location_id
  AND own.prompt_id = answers.prompt_id
  AND own.engine = answers.engine
  AND own.period = answers.period
  AND own.subject_brand_id = answers.client_brand_id
LEFT JOIN links
  ON links.client_brand_id = answers.client_brand_id
  AND links.country_code = answers.country_code
  AND links.location_id = answers.location_id
  AND links.prompt_id = answers.prompt_id
  AND links.engine = answers.engine
  AND links.period = answers.period
LEFT JOIN ref.current_benchmark_prompts AS prompt_tab
  ON prompt_tab.client_brand_id = answers.client_brand_id
  AND prompt_tab.country_code = answers.country_code
  AND prompt_tab.prompt_id = answers.prompt_id
LEFT JOIN ref.current_brands AS brand_names
  ON brand_names.brand_id = members.brand
