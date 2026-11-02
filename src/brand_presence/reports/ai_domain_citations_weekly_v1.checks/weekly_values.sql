SELECT * FROM mart.ai_domain_citations_weekly_v1
WHERE week_start != DATE_TRUNC(week_start, ISOWEEK)
  OR source NOT IN ('cloro', 'peec')
  OR citation_count < prompts_citing OR prompts_citing <= 0 OR avg_citation_position IS NOT NULL
