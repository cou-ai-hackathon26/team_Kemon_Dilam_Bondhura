# Email bill reminders

Members opt in under **Activity → Email bill reminders** after verifying their own address in Settings. The scheduler checks open supplier bills due today or in 1, 3 or 7 days. A digest contains the count, a website link and a workspace-specific unsubscribe link. Amounts, supplier names, invoice numbers and attachments stay in the workspace.

Use the existing mail transport. Set `SAFELEDGER_PUBLIC_URL` to the canonical HTTPS website origin. Configure one of the mailer’s existing providers, for example the free Gmail Apps Script relay using `SAFELEDGER_MAIL_RELAY_URL` and `SAFELEDGER_MAIL_RELAY_SECRET`. Other supported mail providers require a valid sender address. Keep secrets on the API host. The authenticated notification tick must be scheduled for unattended delivery. The Activity refresh also runs a tick. Provider configuration alone does not verify provider acceptance or mailbox delivery.

Members’ Dhaka quiet hours apply to email even if they turn off in-app notifications. Each workspace seat can receive one digest per local day. Each workspace tick queues up to eight new digests and attempts at most two email jobs. The mailer reserves recipient, hourly bulk and daily quota before sending. Budget-limited jobs can resume later that day. Once a transport attempt begins, an ambiguous result is never retried for the same day. A provider’s positive response marks acceptance, not inbox delivery.

Recipients can turn off emails inside Activity or confirm unsubscribe from the email link without signing in. Changing the verified address, losing membership or archiving a workspace revokes consent. A new enrollment is required to resume. A link from an older enrollment cannot disable a new one.

Run `python -m pytest -q api/tests/test_bill_email.py` for isolated tests with a fake mail transport.
