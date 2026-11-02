-- Read each answer once from its client brand row, as the citations reports do.
-- Neither source collects citation order in its clean tables.
WITH links AS (
  SELECT c.run_date, 'cloro' AS source, c.engine, COALESCE(c.keyword_group, 'unknown') AS keyword_group, c.prompt_id,
    cited_url AS link
  FROM mart.ai_prompt_results_v2 AS c
  CROSS JOIN UNNEST(c.cited_urls) AS cited_url
  WHERE c.brand = c.client_brand_id
  UNION ALL
  SELECT p.run_date, 'peec' AS source, p.engine, COALESCE(p.keyword_group, 'unknown') AS keyword_group, p.prompt_id,
    peec_url AS link
  FROM mart.peec_prompt_results_v1 AS p
  CROSS JOIN UNNEST(p.cited_urls) AS peec_url
  WHERE p.brand = p.client_brand_id AND p.citation_known
),
pages AS (
  SELECT DATE_TRUNC(run_date, ISOWEEK) AS week_start, source, engine, keyword_group,
    prompt_id,
    REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(link), '^[a-z][a-z0-9+.-]*:/[/]', '//'), '^//', ''), '[/?#].*$', ''), '^.*@', ''), ':[0-9]*$', ''), '[.]$', ''), '^www[.]', '') AS domain
  FROM links
)
SELECT week_start, source, engine, keyword_group, domain,
  COUNT(*) AS citation_count, COUNT(DISTINCT prompt_id) AS prompts_citing,
  CAST(NULL AS FLOAT64) AS avg_citation_position
FROM pages
WHERE domain != ''
GROUP BY week_start, source, engine, keyword_group, domain
