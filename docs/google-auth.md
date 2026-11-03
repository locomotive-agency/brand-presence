# Google sign-in

Search Console, GA4 and BigQuery use Application Default Credentials (ADC). Google Ads
uses its own environment variables, generated with the same sign-in.

## One-time OAuth client

Reading Search Console or GA4 through ADC needs a custom OAuth client. Google documents
this for [ADC scopes outside Google Cloud](https://docs.cloud.google.com/docs/authentication/application-default-credentials).

1. In your own Google Cloud project, configure the OAuth consent screen. If the app is
   in testing, add your account as a test user.
2. Create an OAuth client of type **Desktop app**.
3. Download its client JSON and keep it outside this repository.

## Sign in

Pick the scopes for the sources you use. This command covers all three Google sources:

```sh
gcloud auth application-default login \
  --client-id-file=/path/to/example-oauth-client.json \
  --scopes=https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/webmasters.readonly,https://www.googleapis.com/auth/analytics.readonly,https://www.googleapis.com/auth/adwords
gcloud auth application-default set-quota-project example-project
```

| Scope | Needed for |
| --- | --- |
| `cloud-platform` | Always. gcloud requires it. |
| `webmasters.readonly` | Search Console API |
| `analytics.readonly` | GA4 API |
| `adwords` | Google Ads refresh token |

Scopes are fixed at sign-in. The kit itself only asks for the read-only API scopes and
BigQuery. The quota project needs **Service Usage Consumer**.

## Service account instead

Set `GOOGLE_APPLICATION_CREDENTIALS` to a service account JSON file kept outside this
repository. Give that account access to each property and to your BigQuery project.

No credential belongs in YAML.
