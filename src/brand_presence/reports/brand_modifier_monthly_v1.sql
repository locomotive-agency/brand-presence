WITH term_latest AS (
  SELECT
    latest.month,
    latest.client_brand_id,
    latest.ad_account_id,
    latest.campaign_id,
    latest.search_term,
    MAX(latest.pulled_at) AS pulled_at
  FROM core.fct_ads_search_terms_v1 AS latest
  GROUP BY latest.month, latest.client_brand_id, latest.ad_account_id, latest.campaign_id,
    latest.search_term
),
terms AS (
  SELECT
    fact.month,
    fact.client_brand_id,
    fact.ad_account_id,
    fact.campaign_id,
    fact.search_term,
    CONCAT(' ', REGEXP_REPLACE(
      TRIM(REGEXP_REPLACE(LOWER(fact.search_term), '[^\\p{L}\\p{N}]+', ' ')), ' ', '  '),
      ' ') AS spaced,
    fact.impressions,
    fact.clicks,
    fact.conversions,
    fact.days_covered,
    fact.pulled_at
  FROM core.fct_ads_search_terms_v1 AS fact
  JOIN term_latest
    ON term_latest.month = fact.month
    AND term_latest.client_brand_id = fact.client_brand_id
    AND term_latest.ad_account_id = fact.ad_account_id
    AND term_latest.campaign_id = fact.campaign_id
    AND term_latest.search_term = fact.search_term
    AND term_latest.pulled_at = fact.pulled_at
  WHERE fact.month >= DATE '2024-01-01'
),
client_names AS (
  SELECT
    named.brand_id,
    TRIM(REGEXP_REPLACE(LOWER(named.name), '[^\\p{L}\\p{N}]+', ' ')) AS words
  FROM ref.current_brands AS named
  UNION ALL
  SELECT
    aliased.brand_id,
    TRIM(REGEXP_REPLACE(LOWER(other_name), '[^\\p{L}\\p{N}]+', ' ')) AS words
  FROM ref.current_brands AS aliased
  CROSS JOIN UNNEST(aliased.aliases) AS other_name
),
name_patterns AS (
  SELECT
    spelled.brand_id,
    CONCAT(' (', ARRAY_TO_STRING(ARRAY_AGG(
      REGEXP_REPLACE(spelled.words, ' ', '  ') ORDER BY
      REGEXP_REPLACE(spelled.words, ' ', '  ') DESC), '|'), ') ') AS pattern
  FROM client_names AS spelled
  WHERE spelled.words != ''
  GROUP BY spelled.brand_id
),
stripped AS (
  SELECT
    terms.month,
    terms.client_brand_id,
    terms.ad_account_id,
    terms.campaign_id,
    terms.search_term,
    terms.spaced,
    REGEXP_REPLACE(terms.spaced, name_patterns.pattern, ' ') AS rest,
    terms.impressions,
    terms.clicks,
    terms.conversions,
    terms.days_covered,
    terms.pulled_at
  FROM terms
  JOIN name_patterns
    ON name_patterns.brand_id = terms.client_brand_id
),
brand_terms AS (
  SELECT
    stripped.month,
    stripped.client_brand_id,
    stripped.ad_account_id,
    stripped.campaign_id,
    stripped.search_term,
    TRIM(REGEXP_REPLACE(stripped.spaced, ' +', ' ')) AS words,
    CONCAT(' ', TRIM(REGEXP_REPLACE(stripped.rest, ' +', ' ')), ' ') AS rest,
    stripped.impressions,
    stripped.clicks,
    stripped.conversions,
    stripped.days_covered,
    stripped.pulled_at
  FROM stripped
  WHERE stripped.rest != stripped.spaced
),
modifiers AS (
  SELECT
    listed.modifier,
    listed.match_order,
    CONCAT(' ', TRIM(REGEXP_REPLACE(LOWER(listed.modifier), '[^\\p{L}\\p{N}]+', ' ')), ' ')
      AS words
  FROM ref.current_modifiers AS listed
),
first_matches AS (
  SELECT
    brand_terms.month,
    brand_terms.client_brand_id,
    brand_terms.ad_account_id,
    brand_terms.campaign_id,
    brand_terms.search_term,
    MIN(modifiers.match_order) AS match_order
  FROM brand_terms
  CROSS JOIN modifiers
  WHERE modifiers.words != '  '
    AND STRPOS(brand_terms.rest, modifiers.words) > 0
  GROUP BY brand_terms.month, brand_terms.client_brand_id, brand_terms.ad_account_id,
    brand_terms.campaign_id, brand_terms.search_term
),
matched AS (
  SELECT
    brand_terms.month,
    brand_terms.client_brand_id,
    COALESCE(chosen.modifier, 'brand only') AS modifier,
    brand_terms.words,
    brand_terms.impressions,
    brand_terms.clicks,
    brand_terms.conversions,
    brand_terms.days_covered,
    brand_terms.pulled_at
  FROM brand_terms
  LEFT JOIN first_matches
    ON first_matches.month = brand_terms.month
    AND first_matches.client_brand_id = brand_terms.client_brand_id
    AND first_matches.ad_account_id = brand_terms.ad_account_id
    AND first_matches.campaign_id = brand_terms.campaign_id
    AND first_matches.search_term = brand_terms.search_term
  LEFT JOIN modifiers AS chosen
    ON chosen.match_order = first_matches.match_order
),
counted AS (
  SELECT
    matched.month,
    matched.client_brand_id,
    matched.modifier,
    COUNT(DISTINCT matched.words) AS search_terms,
    SUM(matched.impressions) AS impressions,
    SUM(matched.clicks) AS clicks,
    SUM(matched.conversions) AS conversions,
    COALESCE(MIN(matched.days_covered), 0) AS days_covered,
    MAX(matched.pulled_at) AS pulled_at
  FROM matched
  GROUP BY matched.month, matched.client_brand_id, matched.modifier
),
account_coverage AS (
  SELECT
    pull.ad_account_id,
    DATE_TRUNC(covered_day, MONTH) AS month,
    COUNT(DISTINCT covered_day) AS days_covered
  FROM stg.google_ads_pulls AS pull
  CROSS JOIN UNNEST(GENERATE_DATE_ARRAY(pull.window_start, pull.window_end)) AS covered_day
  WHERE pull.report = 'search_terms'
  GROUP BY pull.ad_account_id, DATE_TRUNC(covered_day, MONTH)
),
brand_accounts AS (
  SELECT
    account.ad_account_id,
    COALESCE(account.client_brand_id, 'unknown') AS client_brand_id
  FROM ref.current_ad_accounts AS account
),
term_months AS (
  SELECT DISTINCT
    searched.month,
    searched.client_brand_id
  FROM terms AS searched
),
brand_coverage AS (
  SELECT
    term_months.month,
    term_months.client_brand_id,
    MIN(COALESCE(account_coverage.days_covered, 0)) AS days_covered
  FROM term_months
  JOIN brand_accounts
    ON brand_accounts.client_brand_id = term_months.client_brand_id
  LEFT JOIN account_coverage
    ON account_coverage.ad_account_id = brand_accounts.ad_account_id
    AND account_coverage.month = term_months.month
  GROUP BY term_months.month, term_months.client_brand_id
)
SELECT
  counted.month,
  counted.client_brand_id,
  counted.modifier,
  counted.search_terms,
  counted.impressions,
  counted.clicks,
  counted.conversions,
  SAFE_DIVIDE(CAST(counted.conversions AS FLOAT64), counted.clicks) AS conversion_rate,
  counted.month < DATE_TRUNC(CURRENT_DATE(@client_time_zone), MONTH)
    AND counted.days_covered
      = DATE_DIFF(DATE_ADD(counted.month, INTERVAL 1 MONTH), counted.month, DAY)
    AND COALESCE(brand_coverage.days_covered, counted.days_covered)
      = DATE_DIFF(DATE_ADD(counted.month, INTERVAL 1 MONTH), counted.month, DAY)
    AS is_complete_month,
  counted.pulled_at
FROM counted
LEFT JOIN brand_coverage
  ON brand_coverage.month = counted.month
  AND brand_coverage.client_brand_id = counted.client_brand_id
