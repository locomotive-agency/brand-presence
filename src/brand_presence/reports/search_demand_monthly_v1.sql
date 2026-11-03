WITH gsc_routes AS (
  SELECT route_pull.web_property_id, route_pull.client_brand_id, route_pull.route
  FROM stg.search_console_pulls AS route_pull
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY route_pull.web_property_id, route_pull.client_brand_id
    ORDER BY route_pull.pulled_at DESC, route_pull.period DESC, route_pull.route DESC
  ) = 1
),
gsc_route AS (
  SELECT
    api.month,
    api.client_brand_id,
    api.web_property_id,
    api.country_code,
    api.search_type,
    api.row_kind,
    api.query,
    api.term_type,
    api.subject_brand_id,
    api.clicks,
    api.impressions,
    api.position_weighted,
    api.days_covered,
    api.pulled_at
  FROM core.fct_gsc_v1 AS api
  WHERE NOT EXISTS (
    SELECT 1 FROM gsc_routes AS chosen
    WHERE chosen.web_property_id = api.web_property_id
      AND chosen.client_brand_id = api.client_brand_id
      AND chosen.route = 'export'
  )
  UNION ALL
  SELECT
    export.month,
    export.client_brand_id,
    export.web_property_id,
    export.country_code,
    export.search_type,
    export.row_kind,
    export.query,
    export.term_type,
    export.subject_brand_id,
    export.clicks,
    export.impressions,
    export.position_weighted,
    export.days_covered,
    export.pulled_at
  FROM core.fct_gsc_export_v1 AS export
  JOIN gsc_routes AS chosen_export
    ON chosen_export.web_property_id = export.web_property_id
    AND chosen_export.client_brand_id = export.client_brand_id
  WHERE chosen_export.route = 'export'
),
volume_latest AS (
  SELECT
    latest.month,
    latest.client_brand_id,
    latest.country_code,
    latest.location_id,
    latest.keyword,
    MAX(latest.pulled_at) AS pulled_at
  FROM core.fct_search_volume_v1 AS latest
  GROUP BY latest.month, latest.client_brand_id, latest.country_code, latest.location_id,
    latest.keyword
),
volume AS (
  SELECT
    fact.month,
    fact.client_brand_id,
    fact.country_code,
    fact.location_id,
    fact.keyword,
    fact.search_volume,
    fact.pulled_at
  FROM core.fct_search_volume_v1 AS fact
  JOIN volume_latest
    ON volume_latest.month = fact.month
    AND volume_latest.client_brand_id = fact.client_brand_id
    AND volume_latest.country_code = fact.country_code
    AND volume_latest.location_id = fact.location_id
    AND volume_latest.keyword = fact.keyword
    AND volume_latest.pulled_at = fact.pulled_at
),
keywords AS (
  SELECT
    keyword.client_brand_id,
    keyword.country_code,
    keyword.keyword,
    keyword.benchmark_version,
    IF(keyword.term_type = 'generic', 'category', 'brand') AS term_type,
    IF(keyword.term_type = 'generic', '(all)',
      COALESCE(canonical.brand_id, keyword.brand_id)) AS brand
  FROM ref.current_benchmark_keywords AS keyword
  LEFT JOIN ref.current_benchmark_keywords AS canonical
    ON canonical.client_brand_id = keyword.client_brand_id
    AND canonical.country_code = keyword.country_code
    AND canonical.keyword = keyword.canonical_term
  WHERE keyword.search_volume
),
brand_set AS (
  SELECT
    competitor.client_brand_id,
    competitor.country_code,
    competitor.brand_id AS brand
  FROM ref.current_benchmark_competitors AS competitor
  UNION ALL
  SELECT DISTINCT
    own.client_brand_id,
    own.country_code,
    own.client_brand_id AS brand
  FROM ref.current_benchmark_keywords AS own
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
    AND STRPOS(CONCAT(',', ARRAY_TO_STRING(location.sources, ','), ','), ',search-volume,') > 0
),
terms AS (
  SELECT
    volume.month,
    volume.client_brand_id,
    volume.country_code,
    volume.location_id AS geo_id,
    places.geo_level,
    keywords.term_type,
    keywords.brand,
    MAX(keywords.benchmark_version) AS brand_set_version,
    SUM(volume.search_volume) AS search_volume,
    COUNT(DISTINCT IF(volume.search_volume IS NOT NULL, volume.keyword, NULL)) AS keywords_found,
    MAX(volume.pulled_at) AS pulled_at
  FROM volume
  JOIN keywords
    ON keywords.client_brand_id = volume.client_brand_id
    AND keywords.country_code = volume.country_code
    AND keywords.keyword = volume.keyword
  JOIN places
    ON places.location_id = volume.location_id
    AND places.country_code = volume.country_code
  LEFT JOIN brand_set
    ON brand_set.client_brand_id = keywords.client_brand_id
    AND brand_set.country_code = keywords.country_code
    AND brand_set.brand = keywords.brand
  WHERE keywords.term_type = 'category' OR brand_set.brand IS NOT NULL
  GROUP BY volume.month, volume.client_brand_id, volume.country_code, volume.location_id,
    places.geo_level, keywords.term_type, keywords.brand
),
expected AS (
  SELECT
    asked.client_brand_id,
    asked.country_code,
    asked.term_type,
    asked.brand,
    COUNT(DISTINCT asked.keyword) AS keywords_expected
  FROM keywords AS asked
  LEFT JOIN brand_set AS asked_set
    ON asked_set.client_brand_id = asked.client_brand_id
    AND asked_set.country_code = asked.country_code
    AND asked_set.brand = asked.brand
  WHERE asked.term_type = 'category' OR asked_set.brand IS NOT NULL
  GROUP BY asked.client_brand_id, asked.country_code, asked.term_type, asked.brand
),
set_volume AS (
  SELECT
    brand_rows.month,
    brand_rows.client_brand_id,
    brand_rows.country_code,
    brand_rows.geo_id,
    brand_rows.term_type,
    SUM(brand_rows.search_volume) AS brand_set_volume
  FROM terms AS brand_rows
  WHERE brand_rows.term_type = 'brand'
  GROUP BY brand_rows.month, brand_rows.client_brand_id, brand_rows.country_code,
    brand_rows.geo_id, brand_rows.term_type
),
gsc_latest AS (
  SELECT
    latest.month,
    latest.client_brand_id,
    latest.web_property_id,
    latest.country_code,
    latest.search_type,
    latest.row_kind,
    latest.query,
    MAX(latest.pulled_at) AS pulled_at
  FROM gsc_route AS latest
  GROUP BY latest.month, latest.client_brand_id, latest.web_property_id, latest.country_code,
    latest.search_type, latest.row_kind, latest.query
),
queries AS (
  SELECT
    fact.month,
    fact.client_brand_id,
    fact.web_property_id,
    fact.country_code,
    fact.query,
    fact.term_type,
    fact.subject_brand_id,
    fact.clicks,
    fact.impressions,
    fact.position_weighted,
    fact.days_covered,
    fact.pulled_at
  FROM gsc_route AS fact
  JOIN gsc_latest
    ON gsc_latest.month = fact.month
    AND gsc_latest.client_brand_id = fact.client_brand_id
    AND gsc_latest.web_property_id = fact.web_property_id
    AND gsc_latest.country_code = fact.country_code
    AND gsc_latest.search_type = fact.search_type
    AND gsc_latest.row_kind = fact.row_kind
    AND gsc_latest.query = fact.query
    AND gsc_latest.pulled_at = fact.pulled_at
  JOIN ref.current_web_properties AS property
    ON property.web_property_id = fact.web_property_id
    AND property.client_brand_id = fact.client_brand_id
  WHERE fact.row_kind = 'query'
    AND fact.search_type = 'web'
    AND property.source = 'search-console'
),
category_queries AS (
  SELECT DISTINCT
    queries.month,
    queries.client_brand_id,
    queries.web_property_id,
    queries.country_code,
    queries.query,
    queries.clicks,
    queries.impressions,
    queries.position_weighted,
    queries.days_covered,
    queries.pulled_at
  FROM queries
  JOIN ref.current_benchmark_keywords AS generic
    ON generic.client_brand_id = queries.client_brand_id
    AND generic.country_code = queries.country_code
    AND generic.term_type = 'generic'
    AND STRPOS(CONCAT(' ', LOWER(queries.query), ' '),
      CONCAT(' ', LOWER(generic.keyword), ' ')) > 0
  WHERE queries.term_type = 'generic'
),
organic AS (
  SELECT
    brand_queries.month,
    brand_queries.client_brand_id,
    brand_queries.country_code,
    'national' AS geo_level,
    'brand' AS term_type,
    brand_queries.subject_brand_id AS brand,
    SUM(brand_queries.clicks) AS organic_clicks,
    SUM(brand_queries.impressions) AS organic_impressions,
    SUM(brand_queries.position_weighted) AS organic_position_weighted,
    MIN(brand_queries.days_covered) AS organic_days_covered,
    MAX(brand_queries.pulled_at) AS pulled_at
  FROM queries AS brand_queries
  WHERE brand_queries.term_type = 'brand'
    AND brand_queries.subject_brand_id = brand_queries.client_brand_id
  GROUP BY brand_queries.month, brand_queries.client_brand_id, brand_queries.country_code,
    brand_queries.subject_brand_id
  UNION ALL
  SELECT
    category_queries.month,
    category_queries.client_brand_id,
    category_queries.country_code,
    'national' AS geo_level,
    'category' AS term_type,
    '(all)' AS brand,
    SUM(category_queries.clicks) AS organic_clicks,
    SUM(category_queries.impressions) AS organic_impressions,
    SUM(category_queries.position_weighted) AS organic_position_weighted,
    MIN(category_queries.days_covered) AS organic_days_covered,
    MAX(category_queries.pulled_at) AS pulled_at
  FROM category_queries
  GROUP BY category_queries.month, category_queries.client_brand_id,
    category_queries.country_code
)
SELECT
  terms.month,
  terms.client_brand_id,
  terms.country_code,
  terms.geo_id,
  terms.geo_level,
  terms.term_type,
  terms.brand,
  named.name AS brand_name,
  terms.brand_set_version,
  terms.search_volume,
  set_volume.brand_set_volume,
  SAFE_DIVIDE(terms.search_volume, set_volume.brand_set_volume) AS share_of_search,
  year_ago.search_volume AS search_volume_year_ago,
  SAFE_DIVIDE(terms.search_volume - year_ago.search_volume, year_ago.search_volume)
    AS search_volume_yoy_change,
  organic.organic_clicks,
  organic.organic_impressions,
  organic.organic_position_weighted,
  SAFE_DIVIDE(CAST(organic.organic_position_weighted AS FLOAT64), organic.organic_impressions)
    AS organic_avg_position,
  organic.organic_days_covered,
  terms.month < DATE_TRUNC(CURRENT_DATE(@client_time_zone), MONTH)
    AND (organic.organic_days_covered IS NULL
      OR organic.organic_days_covered
        = DATE_DIFF(DATE_ADD(terms.month, INTERVAL 1 MONTH), terms.month, DAY))
    AND terms.keywords_found = expected.keywords_expected
    AS is_complete_month,
  GREATEST(terms.pulled_at, COALESCE(organic.pulled_at, terms.pulled_at)) AS pulled_at,
  expected.keywords_expected,
  terms.keywords_found
FROM terms
LEFT JOIN expected
  ON expected.client_brand_id = terms.client_brand_id
  AND expected.country_code = terms.country_code
  AND expected.term_type = terms.term_type
  AND expected.brand = terms.brand
LEFT JOIN ref.current_brands AS named
  ON named.brand_id = terms.brand
LEFT JOIN set_volume
  ON set_volume.month = terms.month
  AND set_volume.client_brand_id = terms.client_brand_id
  AND set_volume.country_code = terms.country_code
  AND set_volume.geo_id = terms.geo_id
  AND set_volume.term_type = terms.term_type
LEFT JOIN terms AS year_ago
  ON year_ago.month = DATE_SUB(terms.month, INTERVAL 12 MONTH)
  AND year_ago.client_brand_id = terms.client_brand_id
  AND year_ago.country_code = terms.country_code
  AND year_ago.geo_id = terms.geo_id
  AND year_ago.term_type = terms.term_type
  AND year_ago.brand = terms.brand
LEFT JOIN organic
  ON organic.month = terms.month
  AND organic.client_brand_id = terms.client_brand_id
  AND organic.country_code = terms.country_code
  AND organic.geo_level = terms.geo_level
  AND organic.term_type = terms.term_type
  AND organic.brand = terms.brand
WHERE terms.month >= DATE '2024-01-01'
