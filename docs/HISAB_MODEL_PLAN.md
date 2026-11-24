# Hisab AI system and model release plan

The competition demo should show a shop owner typing a payment, checking the draft and asking how it affects next week's cash. Show the same flow with online assistance disabled. Capzoryx can measure which steps ran on the device, used the fine-tuned model or used a backup API.

## Current request paths

| User task | Primary path | Recovery |
| --- | --- | --- |
| Enter a routine sale or payment | On-device parser or qualified tagger, then an editable draft | Ask for an exact amount or let the user enter it |
| Choose Hisab-LM for a BDT payment or receipt | Verify the source amount, date and direction, then call Capzoryx's Qwen3-1.7B adapter on the private Space | If consent and policy allow, try the configured backup API. Otherwise return the verified source fields. Ambiguous inputs require correction. |
| Ask about balance, bills, cash runway or financing | On-device intent model and deterministic finance tools | Use the current workspace figures and surface the source records. The app can ask which period or bill the user meant. |
| Read a printed bill or dictate a payment | Dedicated OCR or on-device speech recognition, then review | Let the owner correct the transcript or fields |

Hosted responses and backup responses use the same draft checks. The backend validates amounts before returning them, and the normal ledger endpoint still checks the workspace role, expected revision and request ID when the owner confirms. Calculations and approvals stay in the finance engine. Nothing should use a language model as the source of a balance, a loan APR or a payment instruction.

## Train the next candidate

The [cash pilot](../ml/hisab_lm/cash_pilot.py) continues the released 1.7B LoRA adapter locally on implicit BDT, compact taka amounts and examples that require abstention, including explicit foreign-currency entries. The 24 engineered examples in the [cash challenge](../evals/fixtures/hisab_cash_challenge.jsonl) are scored before and after. It writes the adapter and a checksummed scorecard to the designated output folder. This pilot checks whether targeted training improves cash extraction. The independent evaluation below governs promotion.

From an environment with PyTorch CUDA, Transformers and PEFT, download the pinned revision specified in `cash_pilot.py` into the local cache. Use a local copy of the released `adapter` directory that includes `adapter_config.json` and `adapter_model.safetensors`.

```sh
python -m ml.hisab_lm.cash_pilot --adapter /path/to/released/adapter --output /path/to/empty/pilot-output
```

1. **Collect representative language.** Start with fictional but natural input from Bangla, Banglish and English speakers. Include lowercase names, implicit BDT, Bangla digits, `1.5k`, lakh/hajar, code-switching and dates. Label ambiguous amounts, negation, multiple payments, questions and foreign currency as cases that require clarification. Keep customer records out of training.
2. **Freeze the test set first.** Keep at least 300 authorized, independently authored sentences separate from training. Group by contributor and paraphrase family so variants cannot cross splits. Record amount in poisha, direction, date and counterparty, or a reason to abstain. Match the [evaluation guide](../evals/README.md) for metadata and overlap checks. The public synthetic v1 data remains a reproducible regression corpus.
3. **Compare candidates on identical inputs.** Run the current 1.7B adapter, the device-only path and an approved provider reference on the same frozen data. Then compare Qwen3-4B-Instruct-2507 as a candidate. Benchmark the base before tuning. Try NF4 QLoRA in the pinned training environment and record the base revision, tokenizer, corpus checksum and config. Reuse the recipe in [the model card](../ml/hisab_lm/MODEL_CARD.md). Read the [Qwen model card](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507) and [PEFT quantization guide](https://huggingface.co/docs/peft/developer_guides/quantization) before changing the recipe.
4. **Test the system as well as raw model output.** Report exact amount, direction, date and party accuracy, false acceptance on ambiguous inputs, coverage, end-to-end p95, and device, private model and API usage. Break results down by language and keep the nil or rejected answers in the denominator. Publish whether the data is synthetic or independently authored. Never send private test records to an external reference without permission.
5. **Release only a measured improvement.** Require at least 95% exact amount and direction, 85% exact named party and zero invented amounts passed to drafts in the challenge suite. Also require a mobile timing test and improved accuracy over the device rules on the frozen set before enabling the trained tagger. Export a checksummed candidate, pin it in a separate deployment, test both successful inference and simulated outages, then promote it with a rollback revision.

## Extend the Assistant after cash extraction

Train separate task adapters or heads for extracting quoted invoice or loan fields and selecting read-only finance tools. Use only allowed tools with typed arguments and validate any proposed call against the current workspace role. For an explanation, pass the verified tool result and its provenance to the renderer. Preview any proposed reminder or ledger change. Confirm it through the existing application workflow. Publish task-specific evaluation; a cash extraction score says little about OCR, loan terms or answer generation.

## Backup and competition rehearsal

Keep the compatible API behind the existing provider gateway. It must be configured for the task, approved to handle record data and disclosed to the owner. Preserve the bounded time and request budgets. Log source, latency and outcome counts without the typed sentence. Leave the rule-based and on-device paths available when a provider times out, returns a mismatched amount or runs out of quota. Shared GPU hosting uses queues and account quotas, so run the same demo with the Space inaccessible. [Hugging Face ZeroGPU limits](https://huggingface.co/docs/hub/spaces-zerogpu)
