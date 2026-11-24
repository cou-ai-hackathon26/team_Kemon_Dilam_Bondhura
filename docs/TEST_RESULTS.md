# Test results

Every check below was run on 24 November 2026 against `main` plus the fixes in this change. CI runs the same
checks on every pull request (`.github/workflows/ci.yml`); the latest `main` run passed.

All tests use synthetic data. No test calls a real AI provider: providers are replaced with mock transports.

## Summary

| Check | Command | Result |
|---|---|---|
| Backend tests | `python -m pytest api/tests -q` | **888 passed** |
| Frontend tests | `npm test --prefix web` | **65 passed** |
| Type-check and production build | `npm run build --prefix web` | passed |
| Real-browser journeys (Chromium) | `node scripts/check_*_browser.cjs`, `node scripts/check_pwa.cjs` | **15 of 15 passed** |
| Parser, currency, language and assistant checks | `node scripts/check_*.cjs` (7 scripts) | 7 of 7 passed |
| Evaluation smoke suite | `python -m evals.run --suite smoke` | passed (exit 0), 0 external calls |

## Challenge 1: a week of spending in one voice note

`api/tests/test_voice_batch.py` has 66 tests. Together they cover:
- amounts said in Bangla, Banglish and English, including number words and Bangla digits;
- days said aloud (gotokal, shombar, "3 din age"), and a chosen week;
- the AI split being checked against what was actually said;
- the rule reader filling in what the AI missed, and taking over when no AI answers;
- the speech fallback order: Groq Whisper, then Gemini, then Hugging Face;
- two payments in one sentence;
- not-paid and promised money, which never become drafts;
- refunds;
- corrections ("500 na na 600").

The real-browser check `scripts/check_voice_batch_browser.cjs` confirmed:
- One note became 5 drafts; the owner edited them and saved 4 together.
- A Bangla recording was kept after a provider failure and retried without recording again.
- Dictation restarted after a disconnection and kept both phrases.
- Voice survives a pause, previews where two models disagree, and saves only after confirmation.
- The public walkthrough works without an account.

## Challenge 2: an honest cash radar when a customer pays late

`api/tests/test_radar.py` has 9 tests:
- Payment habits are learned per customer, then per business, then from the due date.
- A late payer moves the shortfall into view honestly.
- Money a little late is a quiet watch, not an alarm.
- Long-overdue money is not counted as arriving tomorrow.
- There is one alert per episode, and another only when things get clearly worse.
- There are no reminders within a customer's usual habit.
- Old books are rebased, and unpaid bills carry over.
- Plus an end-to-end test of the `/radar` endpoint.

## Claims in the idea document

| Claim | Test | Result |
|---|---|---|
| "9% flat" costs about 17.7% a year | `api/tests/test_idea_claims.py` | 17.74% (1,774 bps) |
| Bill photos become drafts | `web/test/billPhoto.test.ts` (6), `api/tests/test_bill_photo.py` | passed |
| Business records never reach a provider that may keep them without consent | `api/tests/test_fx_assist.py::test_free_provider_cannot_receive_records_even_after_opt_in`, `api/tests/test_bill_photo.py` | passed |
| The AI cannot pay, and injected instructions gain no authority | `api/tests/test_security_execution.py` (60), `api/tests/test_agent.py::test_injection_task_never_adds_privileged_tool`, `api/tests/test_live_evaluation.py` | passed |
| Answers cite the books and refuse off-topic requests | `api/tests/test_ask_books.py::test_sixty_questions_figures_citations_and_off_topic` | passed |
| Offline capture syncs once on reconnect | `scripts/check_pwa.cjs` | passed |

## Real-browser journeys

These run in Chromium against an isolated local API:
- **Rahim (signup to proof):** sign-up, sample cash flow, a collection proof, a task, a public QR code and its
  revocation.
- **Assistant opt-in:** the own model (Hisab-LM) is used only after the owner opts in, its text is kept out of other
  providers, and it returns an unsaved, editable draft.
- **Add records:** one action handles local drafts, the owner is asked before anything is sent online, and nothing is
  saved before confirmation.
- **Payroll:** the owner pays salary and a bonus; an employee joins by a private link and sees only their own
  payslips.
- **Business setup and market:** a new owner sees setup; market figures are sourced or marked unavailable.
- **Voice:** the five voice checks listed under Challenge 1, plus a spoken balance question that creates no draft.
- **Installed app (PWA):** install prompt; cold offline capture with reconnect and replay; share target; private
  routes are not cached; logout cleanup; a bookkeeper cannot receive wallet shares.

## Evaluation suite (`python -m evals.run --suite smoke`)

**Record extraction** uses 18 synthetic sentences: 6 Bangla, 6 Banglish and 6 English.
- Amounts: 18 of 18 correct.
- Direction (money in or out): 18 of 18 correct.
- Party: 8 of 8 correct.

**On-device assistant** is scored on held-out questions that are not in its training data. Failures are listed, not
removed.

| Set | Passed | Pass rate | Confidently wrong |
|---|---|---|---|
| `heldout_eval` | 107 of 110 | 97.3% | 0 |
| `heldout_eval_v2` | 91 of 93 | 97.8% | 0 |
| `heldout_eval_v3` (written last, never tuned on) | 70 of 95 | 73.7% | 6 |

The third set is the honest figure for how well the on-device assistant handles new phrasings. A question it
cannot answer goes to the online assistant, where the owner allows it.

## Bugs found by this audit and fixed

| Bug | Fix | Test |
|---|---|---|
| "500 taka dilam na" / "500 টাকা দিলাম না" (I did **not** give 500) became a 500 expense, both in a voice note and in a typed sentence | A verb followed by "na" means nothing moved. "na na" stays a correction. | `test_not_paid_and_promised_money_never_become_drafts`, `web/test/spokenAmount.test.ts` |
| "I did not pay Rahim 500" became an expense on the phone | Same rule in the phone's reader | `web/test/spokenAmount.test.ts` |
| "rahim 300 taka dibe kal" (Rahim **will** give 300 tomorrow) became an expense dated yesterday | dibe/debe/pabo/পাবো/দিবে are plans, not payments | `test_not_paid_and_promised_money_never_become_drafts` |
| "Karim 700 taka ferot dise" (Karim paid 700 **back**) was money out | Money paid back to the owner is money in | `test_refunds_and_corrections_keep_the_right_direction` |
| A bill photo with only a due date used the due date as the bill date, and the printed due date was never filled in | The due date is read separately and filled in | `web/test/billPhoto.test.ts` |
| An impossible printed date such as 31/02/2026 was accepted | Rejected; today is used, with a warning | `web/test/billPhoto.test.ts` |

## Run them yourself

```bash
python -m pip install -r api/requirements.lock.txt
python -m pytest api/tests -q
npm ci --prefix web && npm run build --prefix web && npm test --prefix web
python -m evals.run --suite smoke
node scripts/check_voice_batch_browser.cjs
```
