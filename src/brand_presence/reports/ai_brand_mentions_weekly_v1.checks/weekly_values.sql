SELECT * FROM mart.ai_brand_mentions_weekly_v1
WHERE week_start != DATE_TRUNC(week_start, ISOWEEK)
  OR source NOT IN ('cloro', 'peec')
  OR prompts_mentioned > prompts_total OR prompts_total <= 0 OR mention_rate < 0 OR mention_rate > 1 OR cited_rate < 0 OR cited_rate > 1 OR avg_mention_position < 1
