# Optional Telegram summaries

In-app alerts work without a bot. To activate Telegram, give the API a dedicated bot token, bot username and random webhook secret using `SAFELEDGER_TELEGRAM_BOT_TOKEN`, `SAFELEDGER_TELEGRAM_BOT_USERNAME`, `SAFELEDGER_TELEGRAM_WEBHOOK_SECRET` and `SAFELEDGER_TELEGRAM_ENABLED=1`. Use a secret of 32–256 ASCII letters, numbers, underscores or hyphens. Register the HTTPS `/api/channels/telegram` endpoint with Telegram's setWebhook method and its secret_token field. `SAFELEDGER_PUBLIC_URL` must point to the workspace website.

Set `SAFELEDGER_TELEGRAM_WEBHOOK_URL` to the full HTTPS webhook URL to let the API register the dedicated bot on startup. Without this setting, register the webhook manually. The startup log reports registration status without showing the token or webhook secret.

An owner opens Settings, requests a five-minute link, starts the private bot chat, verifies the displayed account and confirms delivery. The scheduler sends bill reminders and generic morning and Sunday notices. Sending `/stop` or disconnecting in Settings cancels the link and queued notices.

Turn on **Telegram book replies** separately to send requested totals to the linked private chat. Try `/balance`, `/bills`, `/dues` or `/forecast`. The bot uses deterministic aggregate questions from Ask Your Books and keeps record entry in the authenticated app.
