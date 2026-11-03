# Google API windows with durable local pages

Use Application Default Credentials for GA4 and Search Console. Read Google Ads OAuth
values from environment variables. Keep its developer token optional because Google
retired that requirement in September 2026. No secret belongs in configuration loads.

Keep the source clients, fixed reports, parsers and clean contracts. Exclude the export
routes. Move web properties, ad accounts, modifiers and location mappings into YAML.
Write their references in the same transaction as the immutable configuration load.
Local property and account IDs cannot be rebound across loads. A new binding needs
a new local ID so completed request keys cannot hide a different source account.

Extend the window runner with durable page recovery for Google sources. An abandoned
sending attempt becomes unknown. Look for its deterministic local page path before
repeating a read, and restore pagination state from saved responses. Reuse the original
attempt, request and configuration until the complete answer can be loaded and parsed.
Fail an attempt after a refusal, a non-transient vendor error, or three failed recoveries.
Keep transient and local failures recoverable below that bound, with the recovery count
persisted in the stored request. Failed attempts retain their saved pages; a fresh attempt
uses its own paths and the usual attempt bound. Recovery obeys group refusals, run-ending
checks and the deadline. Restore saved Ads account details before checking a recovered
request, and reuse senders within each snapshot. Clean rows still replace by request key,
never merge.
This extends ADR 002's page identity and leaves Peec's existing retry behavior intact.

A crash before an answer reaches disk may repeat a read and use quota. Saved pages are
not requested again during recovery. Successful windows are not refreshed automatically.

Port only reports whose full dependencies are available. Preserve SQL calculations and
checks. Keep logical project and dataset names bound at execution. The monthly paid
report is excluded because its search-demand input needs Search Console exports.
