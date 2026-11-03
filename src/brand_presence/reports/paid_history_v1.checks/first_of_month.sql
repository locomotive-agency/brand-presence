SELECT paid.month, paid.client_brand_id, paid.country_code, paid.geo_id,
  paid.campaign_type, paid.currency_code
FROM mart.paid_history_v1 AS paid
WHERE paid.month != DATE_TRUNC(paid.month, MONTH)
