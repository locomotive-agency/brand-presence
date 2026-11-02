-- Source marks belong to each brand at the answer benchmark version.
WITH cloro_latest AS (
  SELECT f.month, f.client_brand_id, f.country_code, f.location_id, f.prompt_id, f.engine, f.period, f.subject_brand_id, MAX(f.pulled_at) AS pulled_at
  FROM core.fct_ai_answers_v1 AS f
  GROUP BY f.month, f.client_brand_id, f.country_code, f.location_id, f.prompt_id, f.engine, f.period, f.subject_brand_id
),
cloro_marks AS (
  SELECT f.*
  FROM core.fct_ai_answers_v1 AS f
  JOIN cloro_latest AS n ON n.month = f.month
    AND n.client_brand_id = f.client_brand_id
    AND n.country_code = f.country_code
    AND n.location_id = f.location_id
    AND n.prompt_id = f.prompt_id
    AND n.engine = f.engine
    AND n.period = f.period
    AND n.subject_brand_id = f.subject_brand_id
    AND n.pulled_at = f.pulled_at
),
peec_latest AS (
  SELECT f.mapping, f.answer_key, f.period, f.subject_brand_id, MAX(f.pulled_at) AS pulled_at
  FROM core.fct_peec_answers_v1 AS f
  GROUP BY f.mapping, f.answer_key, f.period, f.subject_brand_id
),
peec_marks AS (
  SELECT f.*
  FROM core.fct_peec_answers_v1 AS f
  JOIN peec_latest AS n ON n.mapping = f.mapping
    AND n.answer_key = f.answer_key
    AND n.period = f.period
    AND n.subject_brand_id = f.subject_brand_id
    AND n.pulled_at = f.pulled_at
  JOIN stg.peec_pulls AS pull ON pull.request_key = f.request_key
    AND pull.period = f.period AND pull.pulled_at = f.source_pulled_at
  WHERE pull.complete_snapshot
),
cloro_received AS (
  SELECT a.client_brand_id, a.country_code, a.location_id, a.prompt_id, a.engine,
    a.period, MAX(a.source_pulled_at) AS source_pulled_at
  FROM cloro_marks AS a
  GROUP BY a.client_brand_id, a.country_code, a.location_id, a.prompt_id, a.engine, a.period
),
cloro_current AS (
  SELECT m.* FROM cloro_marks AS m
  JOIN cloro_received AS r ON r.client_brand_id = m.client_brand_id
    AND r.country_code = m.country_code AND r.location_id = m.location_id
    AND r.prompt_id = m.prompt_id AND r.engine = m.engine AND r.period = m.period
    AND r.source_pulled_at = m.source_pulled_at
),
results AS (
  SELECT DATE_TRUNC(r.run_date, ISOWEEK) AS week_start, 'cloro' AS source,
    r.engine, COALESCE(r.keyword_group, 'unknown') AS keyword_group, r.brand, r.brand_mentioned, r.mention_rank,
    m.cited AS brand_domain_cited
  FROM mart.ai_prompt_results_v2 AS r
  LEFT JOIN cloro_current AS m ON m.client_brand_id = r.client_brand_id
    AND m.country_code = r.country_code AND m.location_id = r.geo_id
    AND m.prompt_id = r.prompt_id AND m.engine = r.engine AND m.period = r.run_id
    AND m.subject_brand_id = r.brand
  UNION ALL
  SELECT DATE_TRUNC(p.run_date, ISOWEEK) AS week_start, 'peec' AS source,
    p.engine, COALESCE(p.keyword_group, 'unknown') AS keyword_group, p.brand, p.brand_mentioned, p.mention_rank,
    pm.cited AS brand_domain_cited
  FROM mart.peec_prompt_results_v1 AS p
  LEFT JOIN peec_marks AS pm ON pm.client_brand_id = p.client_brand_id
    AND pm.country_code = p.country_code AND pm.location_id = p.geo_id
    AND pm.prompt_id = p.prompt_id AND pm.engine = p.engine AND pm.period = p.run_id
    AND pm.subject_brand_id = p.brand
    AND pm.answer_key = p.answer_key
),
counted AS (
  SELECT week_start, source, engine, keyword_group, brand,
    COUNT(*) AS prompts_total,
    SUM(IF(brand_mentioned, 1, 0)) AS prompts_mentioned,
    IF(COUNT(brand_domain_cited) = COUNT(*),
      SAFE_DIVIDE(SUM(IF(brand_domain_cited, 1, 0)), COUNT(*)), NULL) AS cited_rate,
    IF(SUM(IF(brand_mentioned AND mention_rank IS NULL, 1, 0)) > 0, NULL,
      AVG(IF(brand_mentioned, mention_rank, NULL))) AS avg_mention_position
  FROM results
  GROUP BY week_start, source, engine, keyword_group, brand
)
SELECT week_start, source, engine, keyword_group, brand, prompts_total, prompts_mentioned,
  SAFE_DIVIDE(prompts_mentioned, prompts_total) AS mention_rate, cited_rate, avg_mention_position
FROM counted
