-- The domains and pages AI answers cite: one row per month, client brand, country, level and
-- item, made from the cited links of peec_prompt_results_v1. A page is a link as the vendor gave
-- it; a domain is the link's host, lower case, with no www. before it, as the ai-answers
-- source reads a domain. citations counts the month's answers that cite the item, each once:
-- on a domain row, an answer citing two of its pages counts once. engine_count and
-- prompt_count count the engines and the prompts of those answers. rank orders the items of
-- one level by citations, within the month, client brand and country: one more than the
-- items with more citations, so items with as many share a rank. Answers from the verified tracking start.
--
-- Each answer is read once, from its client brand's rows of peec_prompt_results_v1, whose
-- cited links are the same on every brand row of the answer, at the places today's locations
-- tab lists for peec. The month is the vendor observation month.
--
-- is_client_domain and is_competitor_domain are taken from the brand set at each answer's own
-- benchmark version, never today's (rule 1): the client brand's and the competitors' domains
-- as the load that made the version wrote them in ref.brands. A domain is a brand's when it is
-- one of the brand's domains or is under one, as the source reads it. An item is the client's
-- or a competitor's when it is so for any answer of the month that cites it.
WITH results AS (
  SELECT
    result.answer_key, result.run_id,
    result.month,
    result.client_brand_id,
    result.country_code,
    result.prompt_id,
    result.engine,
    result.geo_id,
    result.brand_set_version,
    cited_url AS link,
    MAX(result.pulled_at) AS pulled_at
  FROM mart.peec_prompt_results_v1 AS result
  CROSS JOIN UNNEST(result.cited_urls) AS cited_url
  WHERE result.brand = result.client_brand_id
    AND result.citation_known
  GROUP BY result.answer_key, result.run_id, result.month, result.client_brand_id, result.country_code,
    result.prompt_id, result.engine, result.geo_id, result.brand_set_version, cited_url
),
cited AS (
  SELECT
    linked.answer_key, linked.run_id,
    linked.month,
    linked.client_brand_id,
    linked.country_code,
    linked.prompt_id,
    linked.engine,
    linked.geo_id,
    linked.brand_set_version,
    linked.link,
    REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(
      REGEXP_REPLACE(LOWER(linked.link), '^[a-z][a-z0-9+.-]*:/[/]', '//'),
      '^//', ''), '[/?#].*$', ''), '^.*@', ''), ':[0-9]*$', ''), '[.]$', '') AS host,
    linked.pulled_at
  FROM results AS linked
),
pages AS (
  SELECT
    page.answer_key, page.run_id,
    page.month,
    page.client_brand_id,
    page.country_code,
    page.prompt_id,
    page.engine,
    page.geo_id,
    page.brand_set_version,
    page.link,
    REGEXP_REPLACE(page.host, '^www[.]', '') AS domain,
    page.pulled_at
  FROM cited AS page
  WHERE page.host != ''
),
domains AS (
  SELECT
    answer.answer_key, answer.run_id,
    answer.month,
    answer.client_brand_id,
    answer.country_code,
    answer.prompt_id,
    answer.engine,
    answer.geo_id,
    answer.brand_set_version,
    answer.domain,
    MAX(answer.pulled_at) AS pulled_at
  FROM pages AS answer
  WHERE answer.domain != ''
  GROUP BY answer.answer_key, answer.run_id, answer.month, answer.client_brand_id, answer.country_code,
    answer.prompt_id, answer.engine, answer.geo_id, answer.brand_set_version, answer.domain
),
brand_domains AS (
  SELECT
    listed.load_number,
    listed.brand_id,
    REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(
      REGEXP_REPLACE(REGEXP_REPLACE(LOWER(TRIM(brand_domain)), '^[a-z][a-z0-9+.-]*:/[/]', '//'),
      '^//', ''), '[/?#].*$', ''), '^.*@', ''), ':[0-9]*$', ''), '[.]$', ''),
      '^www[.]', '') AS domain
  FROM ref.brands AS listed
  CROSS JOIN UNNEST(listed.domains) AS brand_domain
),
version_loads AS (
  SELECT
    made.client_brand_id,
    made.country_code,
    made.benchmark_version,
    MIN(made.load_number) AS load_number
  FROM ref.benchmark_sets AS made
  GROUP BY made.client_brand_id, made.country_code, made.benchmark_version
),
set_domains AS (
  SELECT
    version_loads.client_brand_id,
    version_loads.country_code,
    version_loads.benchmark_version,
    own.domain,
    TRUE AS of_client
  FROM version_loads
  JOIN brand_domains AS own
    ON own.load_number = version_loads.load_number
    AND own.brand_id = version_loads.client_brand_id
  UNION ALL
  SELECT
    competitor.client_brand_id,
    competitor.country_code,
    competitor.benchmark_version,
    theirs.domain,
    FALSE AS of_client
  FROM ref.benchmark_competitors AS competitor
  JOIN brand_domains AS theirs
    ON theirs.load_number = competitor.load_number
    AND theirs.brand_id = competitor.brand_id
),
domain_marks AS (
  SELECT
    
    seen.client_brand_id AS marked_brand_id,
    seen.country_code AS marked_country_code,
    seen.brand_set_version AS marked_version,
    seen.domain AS marked_domain,
    LOGICAL_OR(set_domains.of_client) AS of_client,
    LOGICAL_OR(NOT set_domains.of_client) AS of_competitor
  FROM (
    SELECT DISTINCT
      
    marked.client_brand_id,
      marked.country_code,
      marked.brand_set_version,
      marked.domain
    FROM domains AS marked
  ) AS seen
  JOIN set_domains
    ON set_domains.client_brand_id = seen.client_brand_id
    AND set_domains.country_code = seen.country_code
    AND set_domains.benchmark_version = seen.brand_set_version
    AND set_domains.domain != ''
    AND (seen.domain = set_domains.domain
      OR ENDS_WITH(seen.domain, CONCAT('.', set_domains.domain)))
  GROUP BY seen.client_brand_id, seen.country_code, seen.brand_set_version, seen.domain
),
items AS (
  SELECT
    by_domain.month,
    
    by_domain.client_brand_id,
    by_domain.country_code,
    'domain' AS level,
    by_domain.domain,
    '(all)' AS url,
    COUNT(*) AS citations,
    LOGICAL_OR(COALESCE(domain_mark.of_client, FALSE)) AS is_client_domain,
    LOGICAL_OR(COALESCE(domain_mark.of_competitor, FALSE)) AS is_competitor_domain,
    COUNT(DISTINCT by_domain.engine) AS engine_count,
    COUNT(DISTINCT by_domain.prompt_id) AS prompt_count,
    MAX(by_domain.pulled_at) AS pulled_at
  FROM domains AS by_domain
  LEFT JOIN domain_marks AS domain_mark
    ON domain_mark.marked_brand_id = by_domain.client_brand_id
    AND domain_mark.marked_country_code = by_domain.country_code
    AND domain_mark.marked_version = by_domain.brand_set_version
    AND domain_mark.marked_domain = by_domain.domain
  GROUP BY by_domain.month, by_domain.client_brand_id, by_domain.country_code,
    by_domain.domain
  UNION ALL
  SELECT
    by_page.month,
    
    by_page.client_brand_id,
    by_page.country_code,
    'page' AS level,
    by_page.domain,
    by_page.link AS url,
    COUNT(*) AS citations,
    LOGICAL_OR(COALESCE(page_mark.of_client, FALSE)) AS is_client_domain,
    LOGICAL_OR(COALESCE(page_mark.of_competitor, FALSE)) AS is_competitor_domain,
    COUNT(DISTINCT by_page.engine) AS engine_count,
    COUNT(DISTINCT by_page.prompt_id) AS prompt_count,
    MAX(by_page.pulled_at) AS pulled_at
  FROM pages AS by_page
  LEFT JOIN domain_marks AS page_mark
    ON page_mark.marked_brand_id = by_page.client_brand_id
    AND page_mark.marked_country_code = by_page.country_code
    AND page_mark.marked_version = by_page.brand_set_version
    AND page_mark.marked_domain = by_page.domain
  WHERE by_page.domain != ''
  GROUP BY by_page.month, by_page.client_brand_id, by_page.country_code, by_page.domain,
    by_page.link
)
SELECT
  items.month,
  items.client_brand_id,
  items.country_code,
  items.level,
  items.domain,
  items.url,
  items.citations,
  COUNT(above.citations) + 1 AS rank,
  items.is_client_domain,
  items.is_competitor_domain,
  items.engine_count,
  items.prompt_count,
  items.pulled_at
FROM items
LEFT JOIN items AS above
  ON above.month = items.month
  AND above.client_brand_id = items.client_brand_id
  AND above.country_code = items.country_code
  AND above.level = items.level
  AND above.citations > items.citations
GROUP BY items.month, items.client_brand_id, items.country_code, items.level, items.domain,
  items.url, items.citations, items.is_client_domain, items.is_competitor_domain,
  items.engine_count, items.prompt_count, items.pulled_at

