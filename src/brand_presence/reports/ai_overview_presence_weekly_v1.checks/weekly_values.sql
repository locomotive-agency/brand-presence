SELECT * FROM mart.ai_overview_presence_weekly_v1
WHERE week_start != DATE_TRUNC(week_start, ISOWEEK)
  OR source NOT IN ('cloro', 'peec')
  OR queries_with_aio > queries_total OR queries_total <= 0
