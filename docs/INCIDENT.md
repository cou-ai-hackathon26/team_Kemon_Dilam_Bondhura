# Suspected key exposure

1. Set `SAFELEDGER_READ_ONLY=1` on the affected API and restart it. Confirm a normal read returns successfully and a harmless empty POST returns HTTP 503 with `READ_ONLY_MODE`. Logout remains available. Already running requests can finish during deployment.
2. Preserve a private database snapshot and an owner-authorized workspace audit export with the time and chain head. Record which environment, key and providers could have been affected. Treat the snapshot and export as sensitive.
3. Revoke affected sessions using an authorized administrative database connection. Revoke exposed external provider credentials at their issuer. Keep private values out of incident reports.
4. Obtain the owner's recovery decision. In-place replacement of `SAFELEDGER_SIGNING_KEY` breaks historical audit verification, TOTP secrets, customer key unwrapping and pending email codes. Use the signing-key version procedure in SECURITY.md on an isolated copy before activating a new version. Preserve all historical verification and decryption material.
5. Verify audit integrity, sign-in, permissions and cash reconciliation against the isolated copy. Restore write access only after the owner accepts the recovery evidence.

## Website release recovery

The repository owner decides whether to pause a feature or restore a deployment. In **Settings → Data & workspaces → Workspace features**, an owner can pause a tool for their own books. An operator can pause new manual corrections across workspaces with `python -m api.app.feature_flags cash_corrections off` from the API environment. Reports still apply signed corrections already recorded.

Restore the previous verified website deployment in Vercel and check the canonical alias. On Render, restore a deployment that understands the current financial history. Once a signed cash correction exists, the first compatible API release or a newer one is required to produce correct balances. Freeze writes and deploy a forward fix if the previous API cannot read those receipts. Use the isolated QA service and a synthetic workspace for rollback rehearsals. Verify the build revision, database readiness, corrected balance and audit chain before enabling writes.

A local recovery drill on 21 November used a synthetic SQLite database, the same signing key throughout, and a BDT 500 cash entry corrected to BDT 450. Release `666fe64`, its correction-compatible predecessor `5369497` in read-only mode, and the restored `666fe64` each returned BDT 450 and a verified audit chain. The same drill passed for release `425a37f` and its predecessor `666fe64`. Each predecessor rejected writes while frozen. Earlier releases predating signed corrections require a forward fix when the database already contains a correction.

If personal data was involved, the owner assesses the incident and notifies affected users within 72 hours under the project's response plan. Record timing and impact privately.

For the Agentic Mode release, the read-only rehearsal restored API `b7c8fa0` after running `1c2f251` against the same synthetic ledger. Both versions preserved the corrected BDT 450 balance and verified its signed history. This predecessor can read the agent notification kinds, statement settlements and email subscription tables. Restoring writes requires the current reminder-approval endpoint. Keep writes frozen until the API and website are back on compatible releases.
