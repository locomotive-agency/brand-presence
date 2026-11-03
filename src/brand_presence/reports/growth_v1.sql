WITH months AS (
  SELECT DISTINCT demand.month
  FROM mart.search_demand_monthly_v1 AS demand
),
periods AS (
  SELECT
    'month' AS period_type,
    by_month.month AS period_start,
    by_month.month AS period_end,
    1 AS period_months,
    FORMAT_DATE('%b %Y', by_month.month) AS label
  FROM months AS by_month
  UNION ALL
  SELECT DISTINCT
    'quarter' AS period_type,
    DATE_TRUNC(by_quarter.month, QUARTER) AS period_start,
    DATE_ADD(DATE_TRUNC(by_quarter.month, QUARTER), INTERVAL 2 MONTH) AS period_end,
    3 AS period_months,
    FORMAT_DATE('Q%Q %Y', by_quarter.month) AS label
  FROM months AS by_quarter
  UNION ALL
  SELECT DISTINCT
    'half' AS period_type,
    IF(EXTRACT(MONTH FROM by_half.month) > 6,
      DATE_ADD(DATE_TRUNC(by_half.month, YEAR), INTERVAL 6 MONTH),
      DATE_TRUNC(by_half.month, YEAR)) AS period_start,
    IF(EXTRACT(MONTH FROM by_half.month) > 6,
      DATE_ADD(DATE_TRUNC(by_half.month, YEAR), INTERVAL 11 MONTH),
      DATE_ADD(DATE_TRUNC(by_half.month, YEAR), INTERVAL 5 MONTH)) AS period_end,
    6 AS period_months,
    CONCAT(IF(EXTRACT(MONTH FROM by_half.month) > 6, 'H2 ', 'H1 '),
      FORMAT_DATE('%Y', by_half.month)) AS label
  FROM months AS by_half
  UNION ALL
  SELECT DISTINCT
    'year' AS period_type,
    DATE_TRUNC(by_year.month, YEAR) AS period_start,
    DATE_ADD(DATE_TRUNC(by_year.month, YEAR), INTERVAL 11 MONTH) AS period_end,
    12 AS period_months,
    FORMAT_DATE('%Y', by_year.month) AS label
  FROM months AS by_year
  UNION ALL
  SELECT
    'rolling_3' AS period_type,
    DATE_SUB(rolling_3.month, INTERVAL 2 MONTH) AS period_start,
    rolling_3.month AS period_end,
    3 AS period_months,
    CONCAT('3 months to ', FORMAT_DATE('%b %Y', rolling_3.month)) AS label
  FROM months AS rolling_3
  UNION ALL
  SELECT
    'rolling_6' AS period_type,
    DATE_SUB(rolling_6.month, INTERVAL 5 MONTH) AS period_start,
    rolling_6.month AS period_end,
    6 AS period_months,
    CONCAT('6 months to ', FORMAT_DATE('%b %Y', rolling_6.month)) AS label
  FROM months AS rolling_6
  UNION ALL
  SELECT
    'rolling_12' AS period_type,
    DATE_SUB(rolling_12.month, INTERVAL 11 MONTH) AS period_start,
    rolling_12.month AS period_end,
    12 AS period_months,
    CONCAT('12 months to ', FORMAT_DATE('%b %Y', rolling_12.month)) AS label
  FROM months AS rolling_12
),
figures AS (
  SELECT
    periods.period_type,
    periods.period_start,
    periods.period_end,
    periods.period_months,
    periods.label,
    DATE_SUB(periods.period_start, INTERVAL periods.period_months MONTH) AS prior_start,
    DATE_SUB(periods.period_start, INTERVAL 12 MONTH) AS year_ago_start,
    demand.client_brand_id,
    demand.country_code,
    demand.geo_id,
    demand.term_type,
    demand.brand,
    MAX(demand.geo_level) AS geo_level,
    MAX(demand.brand_name) AS brand_name,
    COUNT(DISTINCT demand.month) AS months_in_period,
    LOGICAL_AND(demand.is_complete_month) AS months_complete,
    SUM(demand.search_volume) AS search_volume,
    SUM(demand.brand_set_volume) AS brand_set_volume
  FROM periods
  JOIN mart.search_demand_monthly_v1 AS demand
    ON demand.month BETWEEN periods.period_start AND periods.period_end
  GROUP BY periods.period_type, periods.period_start, periods.period_end,
    periods.period_months, periods.label, demand.client_brand_id, demand.country_code,
    demand.geo_id, demand.term_type, demand.brand
)
SELECT
  figures.period_type,
  figures.period_start,
  figures.period_end,
  IF(figures.months_in_period < figures.period_months OR NOT figures.months_complete,
    CONCAT(figures.label, ' (partial)'), figures.label) AS period_label,
  figures.client_brand_id,
  figures.country_code,
  figures.geo_id,
  figures.geo_level,
  figures.term_type,
  figures.brand,
  figures.brand_name,
  figures.months_in_period,
  figures.search_volume,
  figures.brand_set_volume,
  SAFE_DIVIDE(figures.search_volume, figures.brand_set_volume) AS share_of_search,
  prior.search_volume AS prior_search_volume,
  prior.brand_set_volume AS prior_brand_set_volume,
  SAFE_DIVIDE(prior.search_volume, prior.brand_set_volume) AS prior_share_of_search,
  year_ago.search_volume AS year_ago_search_volume,
  year_ago.brand_set_volume AS year_ago_brand_set_volume,
  SAFE_DIVIDE(year_ago.search_volume, year_ago.brand_set_volume) AS year_ago_share_of_search,
  SAFE_DIVIDE(figures.search_volume - prior.search_volume, prior.search_volume)
    AS search_volume_pop_change,
  SAFE_DIVIDE(figures.search_volume - year_ago.search_volume, year_ago.search_volume)
    AS search_volume_yoy_change,
  SAFE_DIVIDE(figures.search_volume, figures.brand_set_volume)
    - SAFE_DIVIDE(prior.search_volume, prior.brand_set_volume) AS share_of_search_pop_change,
  SAFE_DIVIDE(figures.search_volume, figures.brand_set_volume)
    - SAFE_DIVIDE(year_ago.search_volume, year_ago.brand_set_volume)
    AS share_of_search_yoy_change,
  figures.months_in_period < figures.period_months OR NOT figures.months_complete AS is_partial
FROM figures
LEFT JOIN figures AS prior
  ON prior.period_type = figures.period_type
  AND prior.period_start = figures.prior_start
  AND prior.client_brand_id = figures.client_brand_id
  AND prior.country_code = figures.country_code
  AND prior.geo_id = figures.geo_id
  AND prior.term_type = figures.term_type
  AND prior.brand = figures.brand
LEFT JOIN figures AS year_ago
  ON year_ago.period_type = figures.period_type
  AND year_ago.period_start = figures.year_ago_start
  AND year_ago.client_brand_id = figures.client_brand_id
  AND year_ago.country_code = figures.country_code
  AND year_ago.geo_id = figures.geo_id
  AND year_ago.term_type = figures.term_type
  AND year_ago.brand = figures.brand
