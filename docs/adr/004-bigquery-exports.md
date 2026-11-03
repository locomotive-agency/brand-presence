# Optional property export routes

This supersedes ADR 003's exclusion of exports and its exclusions of dependent reports.
API remains the default. Each web property chooses API or export. Existing API setups
need no new configuration or geo targets file. An export binding names a validated
project and dataset. Address, hostname, source and client brand remain immutable.
An API property can switch route. An export dataset binding cannot be changed after
it has been recorded. The reports use the latest recorded pull route for every date.

Port the fixed export statements, parsers, contracts, report SQL and checks. Execute
export reads in their own location, billed to the configured project, with a per-query
byte cap. Metadata access checks list tables and columns without reading rows.
Keep the source's monthly GA4 spans and Search Console readiness and epoch identities.

Use the existing durable page runner. Save GA4's complete month span with its first
page so recovery cannot shift page numbers when new daily tables appear. Export results
are parsed in Python and written through the same transactional replacement boundary.

Download Google's dated geo targets only through explicit setup. Keep the validated
copy and checksums in local state. Apply the source's country and location validation
only when GA4 export matching needs it. Record its date with configuration snapshots.

Build empty export tables during init so combined reports work without exports.
Preserve report SQL calculations. Keep the engagement report's sessions and
page_views columns as INT64, matching its casts and the source contract. The page
report retains its export-only meaning and optional configured page list.

Tests observe configuration, metadata calls, capped query calls, durable raw and clean
writes, resumed reads, route selection and report results. No credentials or network
are used. Expected report figures come from invented source fixtures.
