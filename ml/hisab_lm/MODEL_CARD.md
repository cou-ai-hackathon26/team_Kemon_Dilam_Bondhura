# Hisab-LM model card

Three base models ran the same 100 synthetic validation prompts on the owner's RTX 5060 Ti (16 GB). Qwen3-1.7B had the highest extraction field accuracy and was selected for tuning. Every baseline missed the project’s schema-validity and invented-number thresholds. Qualification still requires evaluation of a completed fine-tune, an approved reference, CPU latency and native-speaker review.

| Ollama model | Schema valid | Extraction fields correct | Invented numbers | GPU p95 |
| --- | ---: | ---: | ---: | ---: |
| Qwen3-1.7B | 45% | 64.11% | 111 | 1.36 s |
| Qwen3-4B | 36% | 50.61% | 155 | 1.77 s |
| Gemma3-4B | 47% | 59.82% | 134 | 1.28 s |

These are structured-output scores for the first 100 validation rows with the fixed seed 20261121. A separate one-step TinyLlama pilot verified native Windows NF4 LoRA training on 16 synthetic rows. On four validation rows its mean loss fell from 0.969 to 0.778. That hardware pilot uses a different base from Hisab-LM.

## Training data

Use the downloadable MIT-licensed [synthetic training archive](../../web/public/datasets/hisab-lm-synthetic-v1.zip). It contains 3,600 distinct prompts in Bangla, Banglish and English, split into 2,880 training, 360 validation and 360 synthetic test examples. Six equally represented task families cover transaction extraction, printed bill text, quoted loan terms, cash briefings, reminder drafts and selection of read-only financial tools. Bills use generated transcriptions. Each output is checked before inclusion, including replaying tool choices on an in-memory financial dataset. The archive manifest contains per-split checksums. The SHA-256 of this release is `31239d4b85ddb7fdc1aba9c21f5e41cd68f16ef687c19aed0d15bda985e7d0a9`.

Rebuild using the project's pinned backend environment from the repository root.

```sh
python -m ml.hisab_lm.build_dataset --output .private/hisab-lm-synthetic-v1.zip
```

The public test split shares the generator's templates. Publish scores on it as synthetic. Reserve original team-authored material for a separately frozen evaluation. The generator and training job never ingest customer or production records.

## Candidate selection, training and deployment

Run each locally installed Qwen3-1.7B, Qwen3-4B and Gemma3-4B candidate on the same 100 validation examples with `--split validation --limit 100`. Reserve the test split for the chosen, frozen model. Record model and digest, download size, extraction and Bangla accuracy, CPU p95 and hardware. Choose the smallest model with acceptable measured accuracy and review the [licensing record](LICENSES/teachers.md). Use the owner’s CUDA PC or open [the training notebook](train.ipynb) on a free CUDA runtime. It resolves an exact model revision and runs 4-bit LoRA with rank 16, alpha 32, dropout 0.05, effective batch size 16, context 2,048, learning rate 0.0002 and a cosine schedule. It checks validation loss every half epoch across two epochs and exports the best checkpoint. Use the generated training.json and base revision when updating this card.

Place the resulting GGUF beside the Modelfile with the filename it specifies. Run `ollama create hisab-lm -f Modelfile` in that directory. Keep the GGUF and adapters outside Git and retain the chosen model's notices. Deploy to the optional loopback service on the demonstration computer only after review.

```sh
python -m ml.hisab_lm.eval --archive web/public/datasets/hisab-lm-synthetic-v1.zip \
  --ollama-model hisab-lm --predictions .private/hisab-lm-test.jsonl
python -m ml.hisab_lm.eval --archive web/public/datasets/hisab-lm-synthetic-v1.zip \
  --predictions .private/hisab-lm-test.jsonl --reference .private/approved-provider-test.jsonl
```

Prediction files contain one object per selected test ID with the model's raw `text` and optional `latency_ms`. Compare model output against verified structured facts. Review 30 Bangla responses with a native speaker and run the deterministic financial checks for each supported task. The release thresholds are extraction within five percentage points of the best allowed reference, at least 99% schema validity and zero invented numbers. The gateway permits only explicitly enabled local tasks. Keep its task list empty until the task-specific scorecard and privacy review are complete.

## Private weights and the free demo host

For an adapter from the native Windows trainer, run the following from the repository root in the training environment. Point `HF_HOME` at its model cache. Use the converter and CPU quantizer from the same llama.cpp release and install the converter's Python dependencies, including its matching `gguf` package. Keep at least 15 GiB of disk space free for the merge.

```sh
python -m ml.hisab_lm.export_gguf --training /path/to/training --llama-cpp /path/to/llama.cpp --quantizer /path/to/llama-quantize --out /path/to/private/hisab-lm-q4_k_m.gguf
python -m ml.hisab_lm.private_host --receipt /path/to/training/training.json --gguf /path/to/private/hisab-lm-q4_k_m.gguf --upload
```

The uploader uses the signed-in owner's private `capzoryx-hisab-lm` model repository, includes the base license and model revision, then checks the uploaded file's SHA-256. It accepts completed text-model training receipts and a Qwen3 Q4_K_M file. Omitting `--upload` validates the local artifact. Sign in locally with `hf auth login --force` if the prior session has expired. Hugging Face provides 100 GB of private storage to free accounts. [Storage limits](https://huggingface.co/docs/hub/storage-limits)

For the zero-cost inference demo, place the GGUF and Modelfile together on the owner's computer, run `ollama create hisab-lm -f Modelfile`, and use the loopback endpoint `http://127.0.0.1:11434/v1`. Set the gateway's task allowlist only after the independent evaluation described above. The demo runs while that computer is awake. Hugging Face model storage requires no inference endpoint or paid Space.
