-- No count made from a label is set before the stage 2 classification fills the labels:
-- every row with one set is returned.
SELECT
  keyword_row.month, keyword_row.client_brand_id, keyword_row.country_code,
  keyword_row.engine, keyword_row.geo_id, keyword_row.keyword, keyword_row.brand,
  keyword_row.recommended_count, keyword_row.positive_count, keyword_row.neutral_count,
  keyword_row.negative_count
FROM mart.ai_keyword_monthly_v2 AS keyword_row
WHERE keyword_row.recommended_count IS NOT NULL
  OR keyword_row.positive_count IS NOT NULL
  OR keyword_row.neutral_count IS NOT NULL
  OR keyword_row.negative_count IS NOT NULL
