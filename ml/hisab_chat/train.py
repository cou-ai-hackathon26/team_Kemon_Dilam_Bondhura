"""QLoRA fine-tune of Hisab-Chat on one consumer GPU (tuned for an RTX 5060 Ti, 16 GB or 8 GB).

    python -m ml.hisab_chat.train --data data/hisab_chat --output runs/hisab-chat --gguf
    python -m ml.hisab_chat.train --data data/hisab_chat --output runs/hisab-chat-12b --base google/gemma-3-12b-it --gguf

Loss is computed on assistant turns only, so the model learns to answer rather than to repeat the facts block.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path

BASES = {
    'Qwen/Qwen3-4B-Instruct-2507': dict(batch=2, accumulate=8),  # fits a 16 GB T4 with the longer business context
    'google/gemma-3-12b-it': dict(batch=1, accumulate=16),       # heavier, needs 16 GB
    'Qwen/Qwen3-1.7B': dict(batch=4, accumulate=4),              # the free plan: fast on ZeroGPU and usable on a free CPU
}
MAX_LEN = 2560  # business-context examples carry the whole business (profile, products, market, advice)
SEED = 20261121


def load(data):
    manifest = json.loads((data / 'manifest.json').read_text())
    rows = {}
    for name in ('train', 'validation'):
        raw = (data / f'{name}.jsonl').read_bytes()
        if hashlib.sha256(raw).hexdigest() != manifest['splits'][name]['sha256']:
            raise SystemExit(f'{name}.jsonl does not match manifest.json; rebuild the dataset')
        rows[name] = [json.loads(line) for line in raw.splitlines()]
        if any(not r['source'].startswith('synthetic') for r in rows[name]):
            raise SystemExit('Only synthetic conversations belong in this corpus')
    return rows, manifest


END_MARKERS = ('<|im_end|>', '<end_of_turn>', '<|eot_id|>', '</s>')


def encode(tokenizer, messages):
    """Token ids for the whole chat, with labels only on assistant replies (and the marker that ends each one).

    The chat is rendered as text and replies are located by character offsets, so this works with any chat
    template, including ones that render the final turn differently from earlier turns.
    """
    text = tokenizer.apply_chat_template(messages, tokenize=False)
    spans, cursor = [], 0
    for message in messages:
        start = text.find(message['content'], cursor) if message['content'] else -1
        if start < 0:
            continue
        end = cursor = start + len(message['content'])
        if message['role'] == 'assistant':
            end += next((len(m) for m in END_MARKERS if text.startswith(m, end)), 0)
            spans.append((start, end))
    encoded = tokenizer(text, add_special_tokens=False, return_offsets_mapping=True)
    ids = list(encoded['input_ids'])
    labels = [token if any(s <= first < e for s, e in spans) else -100
              for token, (first, _) in zip(ids, encoded['offset_mapping'])]
    if len(ids) > MAX_LEN or all(label == -100 for label in labels):
        return None
    return dict(input_ids=ids, labels=labels)


def compatible(cls, settings):
    """Training settings that work across Transformers releases (v5 removed warmup_ratio and renamed a few)."""
    import inspect
    known = inspect.signature(cls.__init__).parameters
    if 'warmup_ratio' in settings and 'warmup_ratio' not in known:
        settings['warmup_steps'] = settings.pop('warmup_ratio')  # v5: a warmup_steps value below 1 is a ratio
    if 'eval_strategy' in settings and 'eval_strategy' not in known and 'evaluation_strategy' in known:
        settings['evaluation_strategy'] = settings.pop('eval_strategy')
    dropped = sorted(k for k in settings if k not in known)
    if dropped:
        print('Settings not supported by this Transformers release, skipped:', ', '.join(dropped), flush=True)
    return {k: v for k, v in settings.items() if k in known}


def sample(model, tokenizer):
    """Print one answer to a question the training data never contains, so a broken model is caught before upload."""
    try:
        import random
        from ml.hisab_chat import business_data
        ctx, _ = business_data.invent(random.Random(777))
        messages = [{'role': 'system', 'content': business_data.system(ctx)},
                    {'role': 'user', 'content': 'Eid er age amar bikri barate ki ki korte pari? Amar product dekhe bolo.'}]
        from unsloth import FastLanguageModel
        FastLanguageModel.for_inference(model)
        ids = tokenizer.apply_chat_template(messages, add_generation_prompt=True, return_tensors='pt').to('cuda')
        out = model.generate(input_ids=ids, max_new_tokens=220, do_sample=False)
        print('\n=== Sample answer (check it reads well before using the model) ===', flush=True)
        print(tokenizer.decode(out[0][ids.shape[1]:], skip_special_tokens=True), flush=True)
    except Exception as error:  # noqa: BLE001 - the sample is a check, never a reason to lose the trained adapter
        print('Sample answer skipped:', error, flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--data', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--base', choices=sorted(BASES), default='Qwen/Qwen3-4B-Instruct-2507')
    p.add_argument('--epochs', type=int, default=2)
    p.add_argument('--gguf', action='store_true', help='also export a q4_k_m GGUF for llama.cpp or Ollama')
    p.add_argument('--qlora', action='store_true', help='force 4-bit loading (use if 16-bit runs out of memory)')
    p.add_argument('--merged', metavar='OWNER/REPO', help='also upload merged 16-bit weights privately, for a ZeroGPU Space')
    p.add_argument('--push', metavar='OWNER/REPO', help='upload the GGUF and training.json to a private Hugging Face '
                   'model repo (needs HF_TOKEN with write access)')
    a = p.parse_args()
    rows, manifest = load(a.data)

    import unsloth  # noqa: F401 - must load before transformers so its speed-ups apply
    import torch
    if not torch.cuda.is_available():
        raise SystemExit('A CUDA GPU is required. RTX 50-series cards need a PyTorch build for CUDA 12.8 or newer.')
    from datasets import Dataset
    from transformers import Trainer, TrainingArguments
    from unsloth import FastLanguageModel

    torch.backends.cuda.matmul.allow_tf32 = torch.backends.cudnn.allow_tf32 = True
    vram = torch.cuda.get_device_properties(0).total_memory / 2**30
    # On a 16 GB card a 4B model fits in 16-bit: faster than 4-bit (no dequantising) and slightly more accurate.
    full_precision = vram >= 15 and 'gemma-3-12b' not in a.base and not a.qlora
    model, tokenizer = FastLanguageModel.from_pretrained(model_name=a.base, max_seq_length=MAX_LEN,
                                                         load_in_4bit=not full_precision)
    model = FastLanguageModel.get_peft_model(model, r=16, lora_alpha=32, lora_dropout=0.0, bias='none',
                                             target_modules=['q_proj', 'k_proj', 'v_proj', 'o_proj', 'gate_proj', 'up_proj', 'down_proj'],
                                             use_gradient_checkpointing='unsloth', random_state=SEED)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    sets = {name: Dataset.from_list([e for e in (encode(tokenizer, r['messages']) for r in group) if e])
            for name, group in rows.items()}

    def collate(batch):
        width = max(len(r['input_ids']) for r in batch)
        pad = lambda field, value: torch.tensor([r[field] + [value] * (width - len(r[field])) for r in batch])
        mask = torch.tensor([[1] * len(r['input_ids']) + [0] * (width - len(r['input_ids'])) for r in batch])
        return {'input_ids': pad('input_ids', tokenizer.pad_token_id), 'labels': pad('labels', -100), 'attention_mask': mask}

    plan = dict(BASES[a.base])
    if full_precision:
        # Same effective batch of 16. A 24 GB card (L4) needs smaller steps with the long business context.
        plan = dict(batch=8, accumulate=2) if vram >= 35 else dict(batch=4, accumulate=4)
    args = TrainingArguments(**compatible(TrainingArguments, dict(
        output_dir=str(a.output / 'checkpoints'), num_train_epochs=a.epochs, seed=SEED, report_to='none',
        per_device_train_batch_size=plan['batch'], gradient_accumulation_steps=plan['accumulate'],
        per_device_eval_batch_size=plan['batch'], learning_rate=float(os.getenv('HISAB_LR', '1e-4')), lr_scheduler_type='cosine', warmup_ratio=0.03,
        weight_decay=0.01, bf16=torch.cuda.is_bf16_supported(), fp16=not torch.cuda.is_bf16_supported(),
        optim='adamw_8bit', logging_steps=10, eval_strategy='epoch', save_strategy='epoch', save_total_limit=2,
        load_best_model_at_end=True, metric_for_best_model='eval_loss', greater_is_better=False,
        dataloader_num_workers=4, group_by_length=True)))
    trainer = Trainer(model=model, args=args, train_dataset=sets['train'], eval_dataset=sets['validation'],
                      data_collator=collate)
    trainer.train()
    adapter = a.output / 'adapter'
    model.save_pretrained(str(adapter))
    tokenizer.save_pretrained(str(adapter))
    sample(model, tokenizer)
    if a.gguf:
        model.save_pretrained_gguf(str(a.output / 'gguf'), tokenizer, quantization_method='q4_k_m')
    evidence = dict(base=a.base, dataset=manifest['splits'], epochs=a.epochs, seed=SEED, full_precision=full_precision,
                    best_validation_loss=trainer.state.best_metric, gpu=torch.cuda.get_device_name(0))
    (a.output / 'training.json').write_text(json.dumps(evidence, indent=2) + '\n')
    print(json.dumps(evidence))
    if a.push:
        push(a.output, a.push)
    if a.merged:
        # Full-precision weights for a GPU Space (ZeroGPU runs transformers, not GGUF).
        from huggingface_hub import HfApi
        HfApi().create_repo(a.merged, private=True, exist_ok=True)  # private before any weights are uploaded
        model.push_to_hub_merged(a.merged, tokenizer, save_method='merged_16bit', token=os.environ['HF_TOKEN'])
        HfApi().upload_file(path_or_fileobj=str(a.output / 'training.json'), path_in_repo='training.json', repo_id=a.merged,
                            commit_message='Training record')
        print(f'Uploaded merged weights to https://huggingface.co/{a.merged}')


def push(output, repo):
    """Publish the quantised model privately so the Hugging Face Space can serve it with the PC switched off."""
    import shutil
    from huggingface_hub import HfApi
    files = sorted((output / 'gguf').glob('*.gguf'))
    if not files:
        raise SystemExit('No GGUF to upload; run with --gguf')
    # A fixed file name lets the Space find the newest model without code changes.
    shutil.copy(max(files, key=lambda f: f.stat().st_size), output / 'gguf' / 'hisab-chat-q4_k_m.gguf')
    api = HfApi()
    api.create_repo(repo, private=True, exist_ok=True)
    api.upload_file(path_or_fileobj=str(output / 'gguf' / 'hisab-chat-q4_k_m.gguf'), path_in_repo='hisab-chat-q4_k_m.gguf',
                    repo_id=repo, commit_message='Hisab-Chat model')
    api.upload_file(path_or_fileobj=str(output / 'training.json'), path_in_repo='training.json', repo_id=repo,
                    commit_message='Training record')
    print(f'Uploaded to https://huggingface.co/{repo}')


if __name__ == '__main__':
    main()
