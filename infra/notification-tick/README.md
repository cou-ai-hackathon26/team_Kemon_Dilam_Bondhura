# Notification scheduler

The API creates private in-app alerts on a half-hour schedule. Open **Activity → Notification inbox → Refresh alerts** to check the current workspace immediately. Schedulers process active workspaces in batches. A persisted slot claim and channel-specific deduplication keys handle concurrent runs and retries.

Set a fresh random secret of at least 32 characters as `CAPZORYX_TICK_SECRET` on the API. For the primary timer, set the same value with `wrangler secret put CAPZORYX_TICK_SECRET` in this directory, check `API_ORIGIN` against the intended Render service and deploy the Worker using your free Cloudflare account. Its scheduled handler runs at minutes 0 and 30 UTC.

The optional backup workflow runs at minutes 7 and 37 UTC on the default branch. Set the repository variable `CAPZORYX_ENABLE_NOTIFICATION_BACKUP` to `1`. For each deployed environment, configure its GitHub environment with `CAPZORYX_TICK_ORIGIN` as an HTTPS origin and `CAPZORYX_TICK_SECRET` as an environment secret. Environments without a configured origin skip delivery. Keep separate secrets for independent API deployments.

The inbox uses Asia/Dhaka time and defaults to quiet hours from 21:00 to 08:00. New-browser sign-in alerts appear immediately. Bills and customer balances are rechecked before deferred delivery. Customer reminders are drafted and shared separately by the owner.

The staging window and expected website and API revisions are recorded in `.github/staging-website.json`. Publishing a new manifest exercises a synthetic QA workspace once. Scheduled runs check those revisions and Turso readiness until the UTC expiry.
