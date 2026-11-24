# Device notifications

On the website, an authenticated member can turn on push under Activity after the operator configures Web Push. The website installs a notification-only worker scoped to /reminders/ after permission is granted. It opens Activity and leaves all pages and financial traffic outside its scope. The v2 PWA also offers device enrollment. Native Android devices use the same subscription API with an FCM registration token. An authenticated workspace member opts in on each device. Push notifications display a generic invitation to open the workspace. Financial amounts and names stay in the authenticated inbox. Signing out of the PWA removes its device subscription.

The API enables each channel when `SAFELEDGER_PUSH_ENABLED=1` and its corresponding settings are present. Keep private keys in the API host's secret environment.

| Channel | Settings |
| --- | --- |
| Web Push | `SAFELEDGER_VAPID_PRIVATE` (32-byte P-256 private scalar), `SAFELEDGER_VAPID_PUBLIC` (65-byte uncompressed public point), both base64url-encoded without padding. Set `SAFELEDGER_VAPID_SUBJECT` to an HTTPS contact page or mailto contact. |
| Android FCM | `SAFELEDGER_FCM_SERVICE_ACCOUNT_JSON`, standard base64 of Firebase service-account JSON with permission to send messages for the Android app's Firebase project. The Android build separately needs its matching Firebase client configuration. |

Scheduled work uses the shared notification tick. Web Push sends an empty RFC 8030 notification signed with VAPID. FCM uses short-lived scoped OAuth access tokens. Each distinct alert consumes one of the member's three daily push slots in Bangladesh time across devices. The in-app inbox retains additional details. Expired device endpoints are retired.

Run the synthetic transport tests with `python -m pytest api/tests/test_push_channels.py`. They generate ephemeral signing keys, assert the requests and responses in memory and require no provider credentials.
