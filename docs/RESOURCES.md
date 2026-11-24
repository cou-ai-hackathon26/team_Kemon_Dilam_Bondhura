# Free service configuration

| Use | Resource | Configuration |
| --- | --- | --- |
| Website | Vercel Capzoryx, branch main | Project root directory `web`. The public website is https://capzoryx.vercel.app. |
| Installable app | Same Vercel website and branch | Install the web app from https://capzoryx.vercel.app. Android CI builds from the same source on `main`. |
| Live API | Render capzoryx-staging-api | Despite its historical service name, this service and its Turso database serve the live website and app. Synthetic tests run locally and in CI. |
| Model evaluation | Local CPU | The browser parser and deterministic finance engine work without external keys. Train the optional tagger locally. Independently written samples can be collected at /field-kit/index.html. |
| Optional QLoRA | Owner’s local RTX 5060 Ti, or a free CUDA notebook | Compare the base models on the validation split before tuning. Use the final test split after selection. Keep weights, model credentials and private evaluation files outside the public repository. |
| Optional notifications | Cloudflare Workers Free and GitHub Actions | Set separate staging and live tick secrets using the deployment instructions in infra/notification-tick. New alerts are readable in Activity. |
| Optional delivery | VAPID, Firebase or Telegram | Configure only a channel chosen by the owner. Device and recipient consent are required before delivery. |

The hosted app does not require a paid AI key, paid SMS, a Student Pack, or a Play Store account. Student benefits and any free GPU quota are personal account entitlements. The owner can claim them through the relevant provider if eligible. Retain the free plan when quotas run out.

## Release checks

Run synthetic browser and API tests locally or in CI. Check the canonical website's revision, API health and database readiness after deploying. Maintain the existing signing key and live Turso database when rolling a build forward.

For seasonal cash-planning alerts, Pohela Boishakh uses 14 April in Bangladesh. The operator can supply locally agreed Eid planning dates as comma-separated ISO dates in `CAPZORYX_EID_PLANNING_DATES`. Active workspaces receive one owner inbox reminder 30 days before each event.
