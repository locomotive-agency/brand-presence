WITH prompt_versions AS (
 SELECT mapping, prompt_id, version, prompt_text, topic_id
 FROM stg.peec_prompts
 GROUP BY mapping, prompt_id, version, prompt_text, topic_id
),
prompt_observed AS (
 SELECT mapping, prompt_id, MAX(observed_at) AS observed_at
 FROM stg.peec_prompts
 GROUP BY mapping, prompt_id
),
prompt_latest AS (
 SELECT prompt.mapping, prompt.prompt_id, prompt.is_archived
 FROM stg.peec_prompts AS prompt
 JOIN prompt_observed AS latest ON latest.mapping = prompt.mapping
 AND latest.prompt_id = prompt.prompt_id AND latest.observed_at = prompt.observed_at
 GROUP BY prompt.mapping, prompt.prompt_id, prompt.is_archived
),
peec_places AS (
 SELECT place.location_id, place.country_code
 FROM ref.current_locations AS place
 CROSS JOIN UNNEST(place.sources) AS selected_source
 WHERE selected_source = 'peec' AND place.kind = 'country'
),
peec_latest AS (
 SELECT fact.mapping, fact.answer_key, fact.period, fact.subject_brand_id, MAX(fact.pulled_at) AS pulled_at
 FROM core.fct_peec_answers_v1 AS fact
 GROUP BY fact.mapping, fact.answer_key, fact.period, fact.subject_brand_id
),
peec_current AS (
 SELECT fact.month, fact.client_brand_id, fact.country_code, fact.location_id, fact.prompt_id, fact.engine, fact.period, fact.subject_brand_id, fact.period_start, fact.source_pulled_at, fact.benchmark_version, fact.state, fact.named, fact.cited, fact.first_mention_at, fact.mention_rank, fact.answer_snippet, fact.mapping, fact.answer_key, fact.request_key, fact.observation_date, fact.citation_known, fact.prompt_version, fact.vendor_mentioned, fact.vendor_position, fact.pulled_at
 FROM core.fct_peec_answers_v1 AS fact
 JOIN peec_latest AS latest ON latest.mapping = fact.mapping
 AND latest.answer_key = fact.answer_key AND latest.period = fact.period
 AND latest.subject_brand_id = fact.subject_brand_id AND latest.pulled_at = fact.pulled_at
 JOIN stg.peec_pulls AS pull ON pull.request_key = fact.request_key
 AND pull.period = fact.period AND pull.pulled_at = fact.source_pulled_at
 WHERE pull.complete_snapshot
),
peec_members AS (
 SELECT DISTINCT client_brand_id, country_code, benchmark_version, brand_id
 FROM ref.benchmark_competitors
 UNION DISTINCT
 SELECT captured.client_brand_id, captured.country_code, captured.benchmark_version, client.brand_id
 FROM peec_current AS captured
 JOIN ref.current_brands AS client ON client.brand_id = captured.client_brand_id
),
peec_known AS (
 SELECT observed.request_key, observed.period,
 LOGICAL_AND(observed.citation_known) AS citation_known
 FROM peec_current AS observed
 GROUP BY observed.request_key, observed.period
),
peec_links AS (
 SELECT link.mapping, link.answer_key, link.period, link.source_pulled_at,
 ARRAY_AGG(DISTINCT link.link ORDER BY link.link) AS cited_urls,
 MAX(link.pulled_at) AS pulled_at
 FROM core.fct_peec_cited_links_v1 AS link
 JOIN stg.peec_pulls AS pull ON pull.request_key = link.request_key
 AND pull.period = link.period AND pull.pulled_at = link.source_pulled_at
 WHERE pull.complete_snapshot
 GROUP BY link.mapping, link.answer_key, link.period, link.source_pulled_at
)
SELECT
 fact.period AS run_id, fact.observation_date AS run_date, fact.month,
 fact.client_brand_id, fact.country_code, fact.prompt_id,
 CAST(NULL AS STRING) AS keyword, CAST(NULL AS STRING) AS keyword_group,
 CAST(NULL AS STRING) AS intent, fact.engine,
 fact.location_id AS geo_id, 'national' AS geo_level,
 member.brand_id AS brand, brand.name AS brand_name,
 fact.benchmark_version AS brand_set_version, fact.state AS answer_state,
 fact.named AS brand_mentioned, fact.mention_rank,
 CAST(NULL AS BOOL) AS is_recommended, CAST(NULL AS STRING) AS sentiment,
 own.cited AS client_domain_cited, COALESCE(links.cited_urls, []) AS cited_urls,
 fact.answer_snippet, fact.benchmark_version AS prompt_set_version,
 GREATEST(fact.pulled_at, COALESCE(links.pulled_at, fact.pulled_at)) AS pulled_at,
 fact.answer_key, fact.vendor_mentioned, fact.vendor_position,
 known.citation_known, prompt.prompt_text, prompt.topic_id, latest_prompt.is_archived
FROM peec_current AS fact
JOIN peec_members AS member ON member.client_brand_id = fact.client_brand_id
 AND member.country_code = fact.country_code AND member.benchmark_version = fact.benchmark_version
 AND (member.brand_id = fact.subject_brand_id OR fact.subject_brand_id = '(none)')
JOIN peec_known AS known ON known.request_key = fact.request_key AND known.period = fact.period
JOIN peec_places AS place ON place.location_id = fact.location_id
 AND place.country_code = fact.country_code
LEFT JOIN peec_current AS own ON own.mapping = fact.mapping
 AND own.answer_key = fact.answer_key AND own.period = fact.period
 AND own.subject_brand_id = fact.client_brand_id
LEFT JOIN peec_links AS links ON links.mapping = fact.mapping
 AND links.answer_key = fact.answer_key AND links.period = fact.period
 AND links.source_pulled_at = fact.source_pulled_at
JOIN prompt_versions AS prompt ON prompt.mapping = fact.mapping
 AND prompt.prompt_id = fact.prompt_id AND prompt.version = fact.prompt_version
JOIN prompt_latest AS latest_prompt ON latest_prompt.mapping = fact.mapping
 AND latest_prompt.prompt_id = fact.prompt_id
LEFT JOIN ref.current_brands AS brand ON brand.brand_id = member.brand_id

