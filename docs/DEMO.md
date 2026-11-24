# Rahim’s cash-flow demo

Allow three minutes. Use a fresh fictional BDT workspace, choose **Try a sample business**, and leave the reserve at zero. Dates follow the Dhaka calendar. Six days from today is the first breach. Twenty-five days from today is the scheduled customer receipt. The browser golden test reads both dates from the actual sample.

| Time | Show | বাংলা pitch | English pitch |
| --- | --- | --- | --- |
| 0:00–0:25 | Overview | রহিমের হাতে আজ দেড় লাখ টাকা। ছয় দিন পর কাঁচামালের বিল মেটাতে হবে। ক্রেতা টাকা দেবেন আরও পরে। মাঝের দিনগুলো কীভাবে চলবে? | Rahim has Tk 150,000 today. Materials are due in six days. His buyer pays later. Will he have enough cash in between? |
| 0:25–0:55 | Cash flow → forecast | দিন ছয়ে হিসাব শূন্যের নিচে যায়। দুটো মোড়কের বিলও হিসাবে আছে। এক সময় ঘাটতি দাঁড়ায় ৩ লাখ ৬০ হাজার টাকা। | On day six the forecast crosses zero. Two packaging bills also appear. At the lowest point the business is short Tk 360,000. |
| 0:55–1:25 | Find a collection plan → Projected balance | পঁচিশতম দিনে পাওয়ার কথা ছয় লাখ টাকা। এর মধ্যে ৩ লাখ ৬০ হাজার ছয় দিনের মধ্যে পেলে ঘাটতি মিটবে। মাস শেষে তখনও ২ লাখ ৪০ হাজার থাকবে। | The buyer is scheduled to pay Tk 600,000 on day 25. Collecting Tk 360,000 by day six covers the gap. The month still ends at Tk 240,000. |
| 1:25–1:50 | Accept and create collection tasks | রহিম পরিকল্পনাটি গ্রহণ করলেন। এখন আদায়ের কাজ মনে রাখা যাবে। হাতে টাকা আসার পরেই তিনি আসল রসিদ লিখবেন। | Rahim accepts the plan and gets a collection task. His cash balance stays at Tk 150,000 until he receives and records the money. |
| 1:50–2:25 | Activity → Create a signed passport → View passport | রহিম সীমিত হিসাব দেখাতে পারেন। QR খুললে সই যাচাই হয়। এতে জমা ৫০ হাজার, খরচ ২০ হাজার, আর বর্তমান নগদ দেড় লাখ টাকা দেখা যায়। | Rahim can show a counterparty a signed summary. Opening the QR verifies the signature. Recorded receipts total Tk 50,000, expenses Tk 20,000 and cash Tk 150,000. |
| 2:25–2:50 | Revoke link → reload public page | রহিম লিংকটি বন্ধ করলে যাচাই পাতাও সেটি দেখায়। এই হিসাব মালিকের নিজের খাতার। | Rahim revokes the link and the verification page reflects that decision. The figures come from the owner’s own books. |
| 2:50–3:00 | Close | আপনার দোকানে ক্রেতার টাকা আসার আগে কোন বিলটি দিতে হয়? এমন একটি কাল্পনিক পরিস্থিতি দিয়ে Capzoryx পরীক্ষা করুন। | Which bill falls due before your buyer pays? Try Capzoryx with a fictional version of that situation. |

The current balance includes the Tk 120,000 opening balance. Use the published scorecards for model comparisons. The local rules run in this demo. The trained tagger and Hisab-LM have separate human, device and model gates. Rehearse optional voice, wallet sharing, push and Android steps on the designated demo devices before adding them to this script. A push or a delivered reminder requires its configured channel and consent.

## Rehearsal and backup

Run the production build, then `node scripts/check_rahim_browser.cjs` with the backend dependencies installed and Playwright Chromium available. The test creates an ephemeral loopback service, a temporary SQLite database, a fresh signing key and two browser contexts. It verifies the figures above, plan acceptance, a public QR and revocation. Set `CAPZORYX_DEMO_FRAMES` to a local directory to capture the five post-login scenes. Optional `PYTHON` and `CHROMIUM_PATH` select installed runtimes. The Android branch includes a separate emulator instrumentation workflow.

For the presentation, rehearse five times with a stopwatch, scan the QR from another phone, and place a tested offline backup on both demo devices. Obtain the organizers’ written rules for eligibility, internet access, external models and prior code before submission.
