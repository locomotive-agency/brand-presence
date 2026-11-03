WITH latest_loads AS (
  SELECT
    held.location_id,
    MAX(held.load_number) AS load_number
  FROM ref.locations AS held
  GROUP BY held.location_id
),
kinds AS (
  SELECT
    listed.location_id,
    listed.kind
  FROM ref.locations AS listed
  JOIN latest_loads
    ON latest_loads.location_id = listed.location_id
    AND latest_loads.load_number = listed.load_number
)
SELECT
  fact.month, fact.client_brand_id, fact.ad_account_id, fact.campaign_id, fact.country_code,
  fact.geo_level, fact.location_id, fact.location_type, fact.currency_code
FROM core.fct_ads_v1 AS fact
LEFT JOIN kinds
  ON kinds.location_id = fact.location_id
WHERE fact.pulled_at = @pulled_at
  AND (
    fact.geo_level NOT IN ('country', 'region', 'city')
    OR kinds.location_id IS NULL
    OR kinds.kind NOT IN ('country', fact.geo_level)
  )
