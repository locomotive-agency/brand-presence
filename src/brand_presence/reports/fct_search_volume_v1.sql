-- Search volume for each client brand, country, location, keyword and month, as the vendor
-- estimates it. A month that several pulls reported shows the latest pull's figure: the row
-- with the latest pulled_at, and of two with the same time, the later period. The figure is
-- kept as the vendor gave it, null where it gave none. No money column of the clean table
-- is read: cost comes from Google Ads alone.
SELECT
  volume.month,
  volume.client_brand_id,
  volume.country_code,
  volume.location_id,
  volume.keyword,
  volume.term_type,
  volume.subject_brand_id,
  volume.benchmark_version,
  volume.search_volume
FROM stg.search_volume AS volume
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY volume.client_brand_id, volume.country_code, volume.location_id,
    volume.keyword, volume.month
  ORDER BY volume.pulled_at DESC, volume.period DESC
) = 1
