WITH campaigns AS (
  SELECT
    campaign.client_brand_id,
    campaign.ad_account_id,
    campaign.campaign_id,
    campaign.currency_code,
    campaign.channel_type,
    DATE_TRUNC(campaign.date, MONTH) AS month,
    campaign.impressions,
    campaign.eligible_impressions,
    campaign.absolute_top_impressions,
    campaign.absolute_top_eligible_impressions,
    campaign.rank_lost_impressions,
    campaign.budget_lost_impressions,
    campaign.impression_share_bounded
  FROM stg.google_ads_campaigns AS campaign
  QUALIFY DENSE_RANK() OVER (
    PARTITION BY campaign.client_brand_id, campaign.ad_account_id, campaign.date
    ORDER BY campaign.pulled_at DESC, campaign.period DESC
  ) = 1
)
SELECT
  campaigns.month,
  campaigns.client_brand_id,
  campaigns.ad_account_id,
  campaigns.campaign_id,
  campaigns.currency_code,
  MAX(campaigns.channel_type) AS channel_type,
  SUM(campaigns.impressions) AS impressions,
  SUM(IF(campaigns.eligible_impressions IS NULL, NULL, campaigns.impressions))
    AS auction_impressions,
  SUM(campaigns.eligible_impressions) AS eligible_impressions,
  SUM(IF(campaigns.absolute_top_eligible_impressions IS NULL, NULL,
    campaigns.absolute_top_impressions)) AS absolute_top_impressions,
  SUM(campaigns.absolute_top_eligible_impressions) AS absolute_top_eligible_impressions,
  SUM(campaigns.rank_lost_impressions) AS rank_lost_impressions,
  SUM(IF(campaigns.rank_lost_impressions IS NULL, NULL, campaigns.eligible_impressions))
    AS rank_lost_eligible_impressions,
  SUM(campaigns.budget_lost_impressions) AS budget_lost_impressions,
  SUM(IF(campaigns.budget_lost_impressions IS NULL, NULL, campaigns.eligible_impressions))
    AS budget_lost_eligible_impressions,
  LOGICAL_OR(campaigns.impression_share_bounded) AS impression_share_bounded
FROM campaigns
GROUP BY campaigns.month, campaigns.client_brand_id, campaigns.ad_account_id,
  campaigns.campaign_id, campaigns.currency_code
