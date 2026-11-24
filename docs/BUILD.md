# Build notes · 21 November 2026

## Release scope

The application baseline includes owner accounts, two-step sign-in, workspace sharing, manual records, a Bangla/Banglish text parser, 7- and 30-day forecasts, loan comparison, PDF reports and an append-only audit trail. The team’s MIT-licensed components are included under LICENSE. This repository has its own release history. The date on a synthetic transaction is a financial test input.

## Production and local testing

Vercel builds `main` from the `web` directory. The website and installable web app share https://capzoryx.vercel.app and the same Render API. The Android build uses the same branch. Vercel routes API traffic to Render for the canonical hostname. In production, preserve the existing Turso credentials and signing key across deploys. The API starts via `bash render_start.sh`.

The pilot uses a sleeping Render free instance. Keep-alive workflows consume shared instance hours. The React build, local parser and financial calculations need no API credits. Smart assist starts off. Leave cloud credentials, translation billing and paid fallback unset on the free pilot. Stop at free-tier limits.

Before live testing, compare /api/health build_commit with the staged Git commit and verify /api/ready reports libsql. Run writable verification against the local synthetic API. Use read-only endpoint checks on the public deployment.

## Plan audit

The idea document’s five-hour demo and the plan book’s 310-hour roadmap have different completion criteria. A PDF export alone cannot substantiate a signed passport. A text parser alone cannot substantiate speech accuracy or a newly trained model. Evaluate each added capability independently.

| Package | Acceptance evidence |
| --- | --- |
| Capture and speech | Extract a draft, review the amount and counterparty, and persist only after confirmation. Rehearse on synthetic clips and qualify on 30 consented independently spoken clips. |
| Gap-fixer | Simulate changes against the same dated movements, exclude protected obligations and retain source IDs. Report cases where the reserve cannot be restored. |
| Passport | Sign aggregate-only payloads and verify signature, expiry and revocation from a phone. |
| AI gateway and models | Enforce data classes and provider terms, evaluate held-out examples, publish measured scores before enabling model routes. |
| PWA and Android | Test installed shell, offline drafts, logout cleanup and duplicate-free replay. Qualify the Android package on a device. |
| Receivables and alerts | Link settlements to ledger inputs atomically, enforce role checks and verify time zones, quiet hours and reminder deduplication. |

The release checklist includes organizer confirmation of code-reuse rules before any event submission.

## Implemented in this build

- **Photo capture** reads printed English or Bangla bills locally with Tesseract. The worker, OCR engine and language data are served by the frontend. A clearly labelled total becomes an editable draft. Conflicting totals and incompatible currencies require manual review.
- **Text and dictation** support combined lakh/hajar amounts and tense-aware dates. Compatible browsers can use an installed on-device speech pack. The 30-clip voice accuracy gate and Android device test are pending.
- **Collection timing (WP17)** estimates full settlement within 7, 14 or 30 days from today. Owners can expand the estimate under an open customer bill. It compares past fully paid bills that were still open at the same age, adjusting each customer’s observed rate towards the workspace rate with weight n/(n+5). It shows sample counts and withholds percentages when there are no comparable settlements. Run `python -m evals.payment_timing` for chronological synthetic calibration.
- **Customer receivables (WP29)** are available to owners under Invoices. Record a debt once, then confirm each partial or full receipt against it. The receipt adds one cleared cash inflow and reduces the projected amount by the same value. Names and optional reminder numbers are encrypted using a random workspace key. Clearing the workspace erases its customer key and contact rows. Financial records can still be restored for 30 days.
- **Cash-gap planning** compares combinations of up to three early collections against the dated cash projection. Options show the amount, source record and remaining maximum gap. A receipt returns to its original baseline on its due date. These are projections.
- **Cash-flow passports** can be issued and revoked by an owner in Activity. The link and printable QR identify an aggregate report. The verifier checks the Ed25519 signature, expiry and revocation. Signer configuration uses SAFELEDGER_PASSPORT_ACTIVE_KEY and SAFELEDGER_PASSPORT_KEYS. The public key map is shipped in web/public/.well-known/capzoryx-passport-key.json. Keep retired public keys until all links signed with them expire.
- **Mobile app** shares the `main` branch with the responsive website. Install the web app from the same hostname.
- **Cloud privacy** starts with Smart assist off. The on-device parser and bill reader work without an account at an AI provider. Remote extraction and planning require a compatible provider explicitly cleared for free, no-training processing. The staging service has no AI billing credentials.

## Website qualification · 21 November

The public landing page previews Bangla, Banglish and English cash entries locally. A visitor can try the example at `/#try-record` while the API wakes up. Signed-in flows are exercised with synthetic workspaces in CI.

| Where to look | What to verify |
| --- | --- |
| Overview → Cash briefing | Compare the current cash, customer dues and seven-day forecast with the workspace records. Select an income or expense total to see its source entries. |
| Add records → cash corrections | Replace or void a standalone manual cash entry. Confirm the change and check the balance, forecast and signed history. |
| Settings → Profile | Turn on reduced background downloads. |
| Settings → Data & workspaces → Workspace features | Pause issuance of a new passport. Existing public verification and owner revocation remain available. |

The [staging workflow](https://github.com/Pranto-404/Capzoryx/actions/runs/44891914051) checked both deployed revisions, Turso readiness, signup, a BDT 500 entry corrected to BDT 450, the resulting briefing, workspace settings and logout. The separate QA site was used for this earlier run.

## Agentic Mode on the website

Open **Overview → Agentic Mode → Review suggestions** in an owner workspace. The collections card selects up to three distinct overdue customers. It ranks bills with comparable history by the outstanding amount multiplied by the seven-day payment estimate. Bills without an estimate are ordered by their outstanding amounts. Preparing a reminder uses the balance at the time of the request, and the owner decides whether to share each draft. Approval rechecks the current balance and allows one reminder per customer per Bangladesh calendar day.

The scheduled check prepares a verified collection plan when a shortfall first appears or moves earlier. Accepting a plan creates collection tasks. Recording a receipt separately changes cash. The supplier extension tool uses the selected open invoice and the date requested by the owner. Printing or copying the draft leaves the invoice due date intact.

In **Add records → Import a bank or wallet statement**, a positive row can suggest an open customer bill. Check the payer and choose the bill before confirming. The cash entry and its settlement are saved together. **Invoices → Wallet receipts** reviews individually shared payment messages. **Activity** contains in-app due reminders, browser push controls and optional email bill reminders. The email channel requires verified membership and a configured mail transport.

In **Add records → Voice**, the website first checks for a local browser language pack. If needed, the owner can download the pretrained Bengali Vosk pack (about 106 MB) or the English Whisper pack (about 116 MB). Both transcribe on-device. Review the draft, including names and numeric amounts, before recording a transaction. Existing machine-voice fixtures verified the capture and recognition paths. Human speech accuracy remains unmeasured.

The public landing page introduces Agentic Mode. Collection suggestions and reminder approvals are available from the signed-in Overview.

## Hisab-LM in Assistant and Add records

**Add records → Read it** tries the on-device reader. If it needs an online service, the same button changes to **Read online** and tells the owner where the sentence will be sent. In **Assistant**, BDT workspace owners can select **Use Hisab-LM** for one completed payment or receipt. A bare amount uses the workspace currency, so `i paid rahman 500` is valid. For Hisab-LM requests, Capzoryx verifies the amount, direction and date from the sentence, then checks the private Hugging Face Space's prediction. If the model is down or disagrees, the server can still return the verified fields. The source appears alongside the draft. The user confirms any save. Hisab-LM messages in Assistant are excluded from the conversation history passed to other chat providers. Cash forecasts and loan comparisons use the finance engine. Photo reading and speech use their dedicated recognition pipelines.

Set `CAPZORYX_HISAB_HF_TOKEN` on the API host to a token with access to the private Space. Verify that `/api/auth/capabilities` returns `hisab_lm: true`, then use an isolated BDT workspace to check that a synthetic test returns `source: hisab_lm` with the expected amount and that the ledger revision changes only after confirmation. The capability alone confirms configuration.

The fallback API receives the sentence only when the selected button discloses the approved backup, the owner has Smart assist enabled, and the provider is approved for record data. It is checked against the same amount and direction. See [the model and evaluation plan](HISAB_MODEL_PLAN.md) for routing, training and release criteria.

## Free-tier release gates

See docs/RESOURCES.md for the website, app and isolated QA environments. A local keypair can be used in a separate development checkout by replacing the public key map and setting the corresponding private key through the environment.

Keep main protected with pull requests and the backend, frontend and dependency-audit CI jobs. Verify branch protection in the repository settings. Publish model scores, mobile speech claims and native-app distribution only after their device and dataset gates pass.
