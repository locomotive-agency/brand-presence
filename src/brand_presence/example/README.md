# Credential-free example

Run `brand-presence example --output example-output` after installing the package.
Open `example-output/report.html`. Every report definition in the package is built,
including prerequisites and SQL checks. Each report gets a CSV, with its defined
columns in order. The HTML reads three of those CSVs during generation.

`inputs.json` holds invented clean and reference rows based on the test fixtures.
Aardvark Sample and Capybara Sample are fictional. Domains end in `.example`.
Account and property bindings are synthetic placeholders, never working accounts.
The inputs cover search volume, Cloro, Peec, Syften, Search Console and GA4 on both
routes, and Google Ads. They are already parsed rows, so this example exercises
reporting, not vendor authentication or collection.

The fixed report date is 2 March 2026. Fact revisions use 14:00 UTC that day.
No clock, environment variable or local configuration changes these inputs.
The example creates an in-memory BigQuery stand-in. It uses the same SQLGlot and
SQLite support as the tests, then discards its temporary state. It makes no network
calls and does not look up credentials.

Known headline figures, specified independently of the SQL:

- Aardvark keyword search volume is 410, 290 and 320 for December, January and February.
  The generic keyword is 2,400, 1,600 and 1,900 in those months.
- In the week of 2 March, each brand is named in one of two Cloro ChatGPT answers
  in the kettles group, giving 50%. Neither is named in the one AI overview answer.
- Peec's one ChatGPT answer in the week of 23 February names Aardvark and does not
  name Capybara. Those shares are 100% and 0%.
- Syften has two matches in each of the weeks beginning 23 February and 2 March.
  Counts use match dates, not post dates. Every example match is accepted.

`expected/` contains the reviewed CSV outputs. Tests compare bytes exactly, including
headers, row order and line endings. Rows sort lexicographically by their serialised
columns. CSV uses UTF-8 and LF newlines. Dates and timestamps use ISO 8601, booleans
are lowercase, arrays are compact JSON, and null is an empty field. Empty text and
null therefore share a CSV representation.

Floating-point and decimal values round to six decimal places, then drop trailing
zeros. This permits an absolute rounding difference of up to 0.0000005 from the
unrounded result. There is no additional tolerance in the CSV comparison. This is
preview precision; production BigQuery reports retain their declared numeric types.

To change the fixture or a report, first specify the new expected behaviour in a
focused test. Run into a new output directory and inspect any differences from
`expected/`. Only replace expected CSVs after explaining changed figures. Never
regenerate expectations automatically inside a test.

This support covers the packaged reports, not arbitrary BigQuery SQL. The production
workflow still writes to your own BigQuery project for Looker Studio or Claude.
