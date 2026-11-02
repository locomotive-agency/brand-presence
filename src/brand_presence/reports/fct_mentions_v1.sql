-- The posts the client's filters caught, one row per client brand and match, by the month it
-- was matched in, as the mentions source stored them in stg.mention_facts: where the match
-- was made, the community, the part of it and the kind of post, the class of the filter that
-- caught it, the brand the filter is about, the filter's keyword group, and whether the post
-- passed the filter's AI check, where the filter has one.
--
-- The source writes a match once, by its own ID, the clean table's key. Where a rebuild from
-- raw wrote one again, the latest pull of it is read, by the match alone, so each match is one
-- row, under the client brand its tag maps to at that pull. The answer's own time is
-- source_pulled_at. Nothing of the post itself is read: its text, title and link are in
-- stg.mention_text, which expires, and a fact never deletes, so no fact reads it. Nothing of
-- today's configuration is read: the brand, the class and the group are those the filter gave when the
-- match was written.
WITH latest AS (
  SELECT
    pulled.match_id,
    MAX(pulled.pulled_at) AS pulled_at
  FROM stg.mention_facts AS pulled
  GROUP BY pulled.match_id
)
SELECT
  DATE_TRUNC(mention.match_date, MONTH) AS month,
  mention.client_brand_id,
  mention.match_id,
  mention.country_code,
  mention.location_id,
  mention.match_date,
  mention.community,
  mention.sub_community,
  mention.item_type,
  mention.mention_class,
  mention.subject_brand_id,
  mention.keyword_group,
  mention.pulled_at AS source_pulled_at,
  mention.accepted
FROM stg.mention_facts AS mention
JOIN latest
  ON latest.match_id = mention.match_id
  AND latest.pulled_at = mention.pulled_at
