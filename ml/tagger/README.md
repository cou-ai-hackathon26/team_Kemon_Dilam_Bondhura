# Hisab-Tagger

This is an offline linear-chain conditional random field for Bangla, Banglish and English transaction drafts. A fixed-seed generator emits each sentence with token-aligned labels. The Python trainer uses five-fold cross-validation to select L-BFGS regularization from three candidate pairs on a deterministic 1,000-example subsample, fits the selected parameters on the full corpus, prunes weights below 0.01, and exports JSON plus a SHA-256 scorecard. The browser implements Viterbi and forward/backward decoding with ordinary TypeScript.

## Reproduce a training run

Use Python 3.12 and Node 22+. The training dependencies stay in a separate environment from the API.

```sh
python3.12 -m venv .private/tagger-venv
.private/tagger-venv/bin/pip install --require-hashes -r ml/tagger/requirements.lock.txt
.private/tagger-venv/bin/python -m ml.tagger.generate --output .private/tagger-train.jsonl
.private/tagger-venv/bin/python -m ml.tagger.train --data .private/tagger-train.jsonl \
  --output .private/hisab-tagger-20261121.json --version 20261121
.private/tagger-venv/bin/python -m ml.tagger.vectors
npm ci --prefix web
node scripts/check_hisab_tagger.cjs
node ml/tagger/predict.cjs .private/hisab-tagger-20261121.json \
  evals/fixtures/records.jsonl > .private/synthetic-predictions.jsonl
python -m evals.run --suite records --predictions .private/synthetic-predictions.jsonl
```

The JS test compares all 200 stored Python feature vectors and checks label posteriors against an exactly enumerable example. Public synthetic regression scores can guide development. Record the separate team-written accuracy result and run on a mid-range Android phone before enabling this model for owners. Instructions and thresholds are in [the evaluation guide](../../evals/README.md).

## Release

For a qualified model, copy the JSON to `web/public/models/hisab-tagger-YYYYMMDD.json` in the deployment build environment. Set `VITE_HISAB_TAGGER_SHA256` to its scorecard digest and `VITE_HISAB_TAGGER_VERSION` to its eight-digit version. Build with both values. The release accepts models of at most 5 MB matching the pinned digest. Quick Add and the assistant load the model from the site's origin on the first transaction. Only spans with at least 0.8 posterior confidence supply draft fields. Amounts come from parsing the explicit tagged number and multiplier tokens, with integer poisha arithmetic. If tagged and rule-derived amounts disagree the owner must review or rephrase the input. Without a configured artifact the ordinary on-device parser runs directly. Owner confirmation is required to save any draft.

The tagger consumes a synthetic training file offline. Personal corrections are outside this pipeline. Its production release gates include separately obtained permission and redaction before a correction may be used to train a model. Training files, owner information and model binaries belong outside Git.
