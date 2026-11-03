WITH countries AS (
  SELECT
    country.client_brand_id,
    country.ad_account_id,
    country.campaign_id,
    country.country_code,
    country.location_id,
    country.location_type,
    country.currency_code,
    DATE_TRUNC(country.date, MONTH) AS month,
    country.impressions,
    country.clicks,
    country.cost_micros,
    country.conversions
  FROM stg.google_ads_geo AS country
  QUALIFY DENSE_RANK() OVER (
    PARTITION BY country.client_brand_id, country.ad_account_id, country.date
    ORDER BY country.pulled_at DESC, country.period DESC
  ) = 1
),
regions AS (
  SELECT
    region.client_brand_id,
    region.ad_account_id,
    region.campaign_id,
    region.country_code,
    region.location_id,
    region.location_type,
    region.currency_code,
    DATE_TRUNC(region.date, MONTH) AS month,
    region.impressions,
    region.clicks,
    region.cost_micros,
    region.conversions
  FROM stg.google_ads_geo_regions AS region
  QUALIFY DENSE_RANK() OVER (
    PARTITION BY region.client_brand_id, region.ad_account_id, region.date
    ORDER BY region.pulled_at DESC, region.period DESC
  ) = 1
),
cities AS (
  SELECT
    city.client_brand_id,
    city.ad_account_id,
    city.campaign_id,
    city.country_code,
    city.location_id,
    city.location_type,
    city.currency_code,
    DATE_TRUNC(city.date, MONTH) AS month,
    city.impressions,
    city.clicks,
    city.cost_micros,
    city.conversions
  FROM stg.google_ads_geo_cities AS city
  QUALIFY DENSE_RANK() OVER (
    PARTITION BY city.client_brand_id, city.ad_account_id, city.date
    ORDER BY city.pulled_at DESC, city.period DESC
  ) = 1
),
delivery AS (
  SELECT
    countries.month,
    countries.client_brand_id,
    countries.ad_account_id,
    countries.campaign_id,
    countries.country_code,
    'country' AS geo_level,
    countries.location_id,
    countries.location_type,
    countries.currency_code,
    SUM(countries.impressions) AS impressions,
    SUM(countries.clicks) AS clicks,
    SUM(countries.cost_micros) AS cost_micros,
    SUM(CAST(countries.conversions AS NUMERIC)) AS conversions
  FROM countries
  GROUP BY countries.month, countries.client_brand_id, countries.ad_account_id,
    countries.campaign_id, countries.country_code, countries.location_id,
    countries.location_type, countries.currency_code
  UNION ALL
  SELECT
    regions.month,
    regions.client_brand_id,
    regions.ad_account_id,
    regions.campaign_id,
    regions.country_code,
    'region' AS geo_level,
    regions.location_id,
    regions.location_type,
    regions.currency_code,
    SUM(regions.impressions) AS impressions,
    SUM(regions.clicks) AS clicks,
    SUM(regions.cost_micros) AS cost_micros,
    SUM(CAST(regions.conversions AS NUMERIC)) AS conversions
  FROM regions
  GROUP BY regions.month, regions.client_brand_id, regions.ad_account_id,
    regions.campaign_id, regions.country_code, regions.location_id,
    regions.location_type, regions.currency_code
  UNION ALL
  SELECT
    cities.month,
    cities.client_brand_id,
    cities.ad_account_id,
    cities.campaign_id,
    cities.country_code,
    'city' AS geo_level,
    cities.location_id,
    cities.location_type,
    cities.currency_code,
    SUM(cities.impressions) AS impressions,
    SUM(cities.clicks) AS clicks,
    SUM(cities.cost_micros) AS cost_micros,
    SUM(CAST(cities.conversions AS NUMERIC)) AS conversions
  FROM cities
  GROUP BY cities.month, cities.client_brand_id, cities.ad_account_id,
    cities.campaign_id, cities.country_code, cities.location_id,
    cities.location_type, cities.currency_code
),
channels AS (
  SELECT
    campaign.ad_account_id,
    campaign.campaign_id,
    MAX(campaign.channel_type) AS channel_type
  FROM stg.google_ads_campaigns AS campaign
  GROUP BY campaign.ad_account_id, campaign.campaign_id
),
coverage AS (
  SELECT
    pull.ad_account_id,
    CASE pull.report
      WHEN 'geography' THEN 'country'
      WHEN 'geography_regions' THEN 'region'
      ELSE 'city'
    END AS geo_level,
    DATE_TRUNC(covered_day, MONTH) AS month,
    COUNT(DISTINCT covered_day) AS days_covered
  FROM stg.google_ads_pulls AS pull
  CROSS JOIN UNNEST(GENERATE_DATE_ARRAY(pull.window_start, pull.window_end)) AS covered_day
  WHERE pull.report IN ('geography', 'geography_regions', 'geography_cities')
  GROUP BY pull.ad_account_id, geo_level, month
),
ever_listed AS (
  SELECT
    held.location_id
  FROM ref.locations AS held
  GROUP BY held.location_id
)
SELECT
  delivery.month,
  delivery.client_brand_id,
  delivery.ad_account_id,
  delivery.campaign_id,
  delivery.country_code,
  delivery.geo_level,
  delivery.location_id,
  delivery.location_type,
  delivery.currency_code,
  channels.channel_type,
  delivery.impressions,
  delivery.clicks,
  delivery.cost_micros,
  delivery.conversions,
  coverage.days_covered
FROM delivery
LEFT JOIN channels
  ON channels.ad_account_id = delivery.ad_account_id
  AND channels.campaign_id = delivery.campaign_id
LEFT JOIN coverage
  ON coverage.ad_account_id = delivery.ad_account_id
  AND coverage.geo_level = delivery.geo_level
  AND coverage.month = delivery.month
LEFT JOIN ever_listed
  ON ever_listed.location_id = delivery.location_id
