"""Three small headline views read from the example CSVs."""

import csv
from collections import Counter
from datetime import date, timedelta
from html import escape


def read(output, name):
    with (output / f'{name}.csv').open(newline='', encoding='utf-8') as stream:
        return list(csv.DictReader(stream))


def table(headers, rows):
    heading = ''.join(f'<th scope="col">{escape(str(h))}</th>' for h in headers)
    body = ''.join('<tr>' + ''.join(f'<td>{escape(str(c))}</td>' for c in row) + '</tr>'
                   for row in rows)
    return f'<table><thead><tr>{heading}</tr></thead><tbody>{body}</tbody></table>'


def write_html(output):
    volume = read(output, 'search_volume_history_v1')
    search = table(['Month', 'Keyword', 'Country', 'Searches'], [
        [r['month'], r['keyword'], r['country_code'], r['search_volume'] or 'Unknown']
        for r in volume])
    ai = table(['Week', 'Source', 'Engine', 'Group', 'Brand', 'Named / answers', 'Share'], [
        [r['week_start'], r['source'], r['engine'], r['keyword_group'], r['brand'],
         f"{r['prompts_mentioned']} / {r['prompts_total']}",
         f"{float(r['mention_rate']) * 100:.1f}%" if r['mention_rate'] else 'Unknown']
        for r in read(output, 'ai_brand_mentions_weekly_v1')])
    counts = Counter()
    for row in read(output, 'fct_mentions_v1'):
        day = date.fromisoformat(row['match_date'])
        week = day - timedelta(days=day.weekday())
        counts[(week.isoformat(), row['mention_class'])] += 1
    mentions = table(['Week beginning', 'Filter class', 'Matches'],
                     [[*key, count] for key, count in sorted(counts.items())])
    html = f'''<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Brand Presence example</title>
<style>
body {{font:16px/1.5 system-ui,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem;
color:#182638;background:#fafbfc}} table {{border-collapse:collapse;width:100%;background:white}}
th,td {{text-align:left;padding:.6rem;border-bottom:1px solid #ccd4df}}
th {{background:#e8eef5}} section {{margin:2rem 0;overflow-x:auto}}
a {{color:#174d83}} h1,h2 {{line-height:1.2}}
</style><main><h1>Brand Presence example</h1>
<p>Invented observations for Aardvark Sample. No live measurements. Built from the CSVs
beside this file. The kit normally writes to your BigQuery project for Looker Studio or Claude.</p>
<section><h2>Search volume trend</h2>
<p>Monthly estimates for each keyword in one country. Missing values mean unknown.</p>{search}
<p><a href="search_volume_history_v1.csv">Search volume CSV</a></p></section>
<section><h2>AI answer brand share</h2>
<p>The share of observed answers naming each brand, including unknown answers in the denominator.
A response can name several brands, so shares need not sum to 100%. Sources and engines
stay separate.
This measures mentions, not recommendations or sentiment.</p>{ai}
<p><a href="ai_brand_mentions_weekly_v1.csv">AI mentions CSV</a></p></section>
<section><h2>Mentions by week</h2>
<p>Syften matches grouped by match date and filter class. A post matching two filters counts twice.
These counts include every acceptance state. They are not sentiment or Reddit reports.</p>{mentions}
<p><a href="fct_mentions_v1.csv">Mention facts CSV</a></p></section></main></html>
'''
    (output / 'report.html').write_text(html, encoding='utf-8')
