# Contributing to Capzoryx

Thank you for helping. A few rules keep the books trustworthy:

1. **Work on a branch** and open a pull request to `main`; `main` deploys to production.
2. **Money is integer minor units.** Never update or delete postings, ledger entries, audit events or approvals; fix
   mistakes with a correction entry.
3. **No model writes a number, moves money or approves a payment.** Figures come from the finance engine; any model text
   that states an amount must pass the number check in `api/app/chat/llm.py`.
4. **Secrets live in environment variables only.** Never commit keys, tokens, model weights or real customer data.
5. **Every user-facing string goes through `t()`**, then run `node scripts/extract_ui_strings.cjs`. No inline styles or
   scripts (strict CSP).
6. **New Python packages** go into `api/requirements.lock.txt` with their pins.

## Before you open a pull request

```bash
python -m pytest api/tests -q
npm run build --prefix web && npm test --prefix web
node scripts/check_i18n.cjs && node scripts/extract_ui_strings.cjs --check
python scripts/security_scan.py
```

For interface changes, also run `node scripts/check_rahim_browser.cjs` and attach a screenshot.
Describe what changed and how you checked it; the pull request template lists the sections.
