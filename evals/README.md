# Offline evaluations

Install the locked web dependencies with `npm ci --prefix web`. From the repository root, run `python -m evals.run --suite smoke`. The command evaluates the actual browser parser on 18 fictional records and runs the three existing local-assistant suites. It requires Python 3.10+, Node and Git, makes zero provider calls, and prints JSON to stdout. CI runs this command for both application branches. See [dataset inventory](../ml/DATA_SOURCES.md).

Use `--suite records` or `--suite assistant` for one family. Each extraction report records the Git commit, implementation checksums, corpus digest, language counts, exact-match totals and case IDs for errors. Party accuracy counts statements with a labelled counterparty and compares case-folded NFC names with normalized whitespace. An abstention counts as an error for each applicable field. Source text stays in the local input file.

## Evaluate a candidate on independent contributions

Have contributors write their own fictional Bangla, Banglish or English transactions using invented names and amounts. Keep their permission and identities separate from the test files. Freeze the labels and dataset digest before running the candidate. Use `evals/fixtures/records.jsonl` for the field format. Human rows use `source` set to `team` and an extra pseudonymous `contributor_ref` such as `p03`. Each `as_of` is an ISO reference date in Dhaka. Each amount is in BDT poisha. Mark unpaid bills as `due`. Use null for an unspecified party. The collection lead checks authorship, permission and removal of real personal data. The harness checks the submitted metadata, uniqueness and normalized training overlap.

Run the candidate and rule-only baseline independently with only `id`, `text` and `as_of` as input. Each prediction file has exactly one JSON object per line with fields `id`, `amount_minor`, `direction` and `party`. For an abstention, keep the ID and use null values. Preserve the rule-only baseline predictions before changing routing to a tagger. The runner can also produce baseline outputs in memory by invoking the current browser parser if the baseline file argument is omitted.

```sh
python -m evals.run --suite team \
  --dataset .private/team-holdout.jsonl \
  --training-data .private/training.jsonl \
  --predictions .private/candidate.jsonl \
  --baseline .private/rules.jsonl
```

Include every training sentence in the training manifest as an object with a `text` field. Evaluation inputs must remain disjoint from training and tuning. The accuracy gate requires at least 300 independently written test sentences, amount and direction accuracy of at least 95%, and named-party accuracy of at least 85%. The candidate must improve on the rule baseline for all three. A physical mid-range Android timing measurement of at most 20 ms is a separate release gate. Save reports locally via shell redirection. Exit status 0 indicates the evaluated checks passed, 1 indicates failed or missing evidence, and 2 indicates invalid input or a runtime failure.

Provider smoke, clip transcription, wallet matching and Android timing have separate inputs and acceptance checks. The local smoke report covers the extraction and local-assistant suites only.

### Voice capture

Create 30 labelled machine-voiced smoke clips locally using FFmpeg and a QA-only installation of `espeakng-loader==0.2.4`.

```sh
python evals/voice/make_smoke.py Capzoryx-Machine-Voice-Smoke.zip
```

The archive contains 16 kHz mono WAVs and `manifest.jsonl`. English and Latin-script Banglish use an English synthetic voice. বাংলা uses a Bengali synthetic voice. The release gate needs a separate corpus of independently spoken clips.

The speech adapter accepts an OpenAI-compatible `POST /v1/audio/transcriptions`. Set `SAFELEDGER_ASR_MODEL` to the installed model and `SAFELEDGER_LOCAL_ASR_URL` to its loopback base URL ending in `/v1`. A cloud deployment can instead use the existing HTTPS-compatible provider with confirmed free, no-training terms and `SAFELEDGER_ASR_ZERO_RETENTION_CONFIRMED=1` after verifying that uploaded audio is discarded. Keep provider credentials in service environment variables.

For a human evaluation, download the field kit's labelled metadata and audio into a private local directory. Use at least 30 consented recordings with pseudonymous speaker references. External processing additionally requires explicit per-clip consent naming that provider in `consented_provider_host`. The evaluation rejects duplicate clips, repeated sentences, and overlap with training sentences or speakers. Run with FFmpeg and the installed frontend dependencies.

```sh
python -m evals.voice.run /path/to/independent-clips --training-manifest /path/to/training.jsonl
```

The output contains the corpus checksum, field accuracies, model, speech service and p95 latency. Set `SAFELEDGER_ASR_EVAL_JSON` to this measured report once the independent gate passes. The production API checks the same model and host, at least 90% amount and kind accuracy, 80% counterparty accuracy, and p95 below six seconds. Owners then enable Smart assist and explicitly send each reviewed clip. Audio stays in request memory. The transcript opens as editable text before record confirmation.
