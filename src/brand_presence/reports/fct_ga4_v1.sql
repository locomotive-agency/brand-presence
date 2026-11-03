WITH engagement AS (
  SELECT
    country.client_brand_id,
    country.web_property_id,
    country.country_code,
    country.location_id,
    country.channel_group,
    country.date,
    DATE_TRUNC(country.date, MONTH) AS month,
    country.period,
    country.sessions,
    country.engaged_sessions,
    country.engagement_seconds,
    country.event_count,
    country.key_events,
    country.page_views
  FROM stg.ga4_engagement AS country
  QUALIFY DENSE_RANK() OVER (
    PARTITION BY country.client_brand_id, country.web_property_id, country.country_code,
      country.date
    ORDER BY country.pulled_at DESC, country.period DESC
  ) = 1
),
cities AS (
  SELECT
    city.client_brand_id,
    city.web_property_id,
    city.country_code,
    city.location_id,
    city.date,
    DATE_TRUNC(city.date, MONTH) AS month,
    city.period,
    city.sessions,
    city.engaged_sessions,
    city.engagement_seconds,
    city.event_count,
    city.key_events,
    city.page_views
  FROM stg.ga4_engagement_cities AS city
  QUALIFY DENSE_RANK() OVER (
    PARTITION BY city.client_brand_id, city.web_property_id, city.country_code, city.date
    ORDER BY city.pulled_at DESC, city.period DESC
  ) = 1
),
country_days AS (
  SELECT web_property_id, month, COUNT(DISTINCT date) AS days_covered
  FROM engagement
  GROUP BY web_property_id, month
),
city_days AS (
  SELECT web_property_id, month, COUNT(DISTINCT date) AS days_covered
  FROM cities
  GROUP BY web_property_id, month
),
country_rows AS (
  SELECT
    engagement.month,
    engagement.client_brand_id,
    engagement.web_property_id,
    engagement.country_code,
    engagement.location_id,
    engagement.channel_group,
    SUM(engagement.sessions) AS sessions,
    SUM(engagement.engaged_sessions) AS engaged_sessions,
    SUM(engagement.engagement_seconds) AS engagement_seconds,
    SUM(engagement.event_count) AS event_count,
    SUM(engagement.key_events) AS key_events,
    SUM(engagement.page_views) AS page_views,
    CASE
      WHEN LOGICAL_OR(pull.sampled) THEN TRUE
      WHEN SUM(IF(pull.sampled IS NULL, 1, 0)) = 0 THEN FALSE
    END AS sampled
  FROM engagement
  LEFT JOIN stg.ga4_pulls AS pull
    ON pull.web_property_id = engagement.web_property_id
    AND pull.period = engagement.period
    AND pull.report = 'engagement'
  GROUP BY engagement.month, engagement.client_brand_id, engagement.web_property_id,
    engagement.country_code, engagement.location_id, engagement.channel_group
),
city_rows AS (
  SELECT
    cities.month,
    cities.client_brand_id,
    cities.web_property_id,
    cities.country_code,
    cities.location_id,
    SUM(cities.sessions) AS sessions,
    SUM(cities.engaged_sessions) AS engaged_sessions,
    SUM(cities.engagement_seconds) AS engagement_seconds,
    SUM(cities.event_count) AS event_count,
    SUM(cities.key_events) AS key_events,
    SUM(cities.page_views) AS page_views,
    CASE
      WHEN LOGICAL_OR(pull.sampled) THEN TRUE
      WHEN SUM(IF(pull.sampled IS NULL, 1, 0)) = 0 THEN FALSE
    END AS sampled
  FROM cities
  LEFT JOIN stg.ga4_pulls AS pull
    ON pull.web_property_id = cities.web_property_id
    AND pull.period = cities.period
    AND pull.report = 'engagement_cities'
  GROUP BY cities.month, cities.client_brand_id, cities.web_property_id,
    cities.country_code, cities.location_id
)
SELECT
  country_rows.month,
  country_rows.client_brand_id,
  country_rows.web_property_id,
  country_rows.country_code,
  'country' AS geo_level,
  country_rows.location_id,
  country_rows.channel_group,
  country_rows.sessions,
  country_rows.engaged_sessions,
  country_rows.engagement_seconds,
  country_rows.event_count,
  country_rows.key_events,
  country_rows.page_views,
  country_days.days_covered,
  country_rows.sampled
FROM country_rows
JOIN country_days
  ON country_days.web_property_id = country_rows.web_property_id
  AND country_days.month = country_rows.month
LEFT JOIN ref.current_locations AS location
  ON location.location_id = country_rows.location_id
UNION ALL
SELECT
  city_rows.month,
  city_rows.client_brand_id,
  city_rows.web_property_id,
  city_rows.country_code,
  'city' AS geo_level,
  city_rows.location_id,
  '(all)' AS channel_group,
  city_rows.sessions,
  city_rows.engaged_sessions,
  city_rows.engagement_seconds,
  city_rows.event_count,
  city_rows.key_events,
  city_rows.page_views,
  city_days.days_covered,
  city_rows.sampled
FROM city_rows
JOIN city_days
  ON city_days.web_property_id = city_rows.web_property_id
  AND city_days.month = city_rows.month
