# Dataset inventory

| Dataset | Origin and permitted use | Split and limitations |
| --- | --- | --- |
| `evals/fixtures/records.jsonl` | 18 fictional transactions authored for this repository, under its MIT license | Public synthetic regression examples in Bengali script, Banglish and English. The parser was improved using this set. |
| `data/local_assistant/intents.json` | Project intent-training phrases, distributed with the repository under MIT | Used to train the local intent classifier. |
| `data/local_assistant/heldout_eval*.json` | Fictional questions about a synthetic workspace, distributed with the repository under MIT | v1 and v2 are development sets. The v3 result is a small generalization check for the intent classifier. |
| `data/fixtures/` | Project-generated business records from `scripts/build_fixtures.py`, under MIT | Financial engine fixtures generated with a fixed seed. |
| `ml/tagger/generate.py` | MIT-licensed project generator with a fixed seed and fictional names, dates, amounts and transaction templates | Synthetic CRF training only. The repository includes the recipe and parity fixtures. |
| Team-written extraction holdout | Voluntary contributions containing only invented businesses, transactions and names. Record the contributor's permission for internal evaluation separately. | Keep each contribution in the private test set. Record the digest of the frozen set before evaluating candidate models. |

Keep consent records, contributor identities and private datasets outside Git. Each new source needs an entry recording ownership, permission or license, intended use and split before training or evaluating with it.

The Hisab-LM archive consists of 3,600 project-generated financial instruction examples and includes its MIT license and split digests. The trainer ingests its train and validation splits. Its public synthetic test split measures template coverage. [Model card and reproduction steps](hisab_lm/MODEL_CARD.md)

The downloadable `web/public/datasets/bangla-practice-v1.zip` bundles 30,000 synthetic token-labelled training examples (10,000 each in Bangla, Banglish and English) from the fixed seed 20261121, the private collection form and Bengali instructions. The manifest contains the training file's SHA-256. The 30 WAV files in `web/public/datasets/machine-voice-smoke-v1.zip` are machine-generated test inputs for audio transport. Human voices, transcripts and independent evaluation data must be kept private and separate from these public practice archives.
