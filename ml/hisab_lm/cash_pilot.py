"""Continue the released cash adapter on synthetic shorthand. Compare on a frozen challenge."""
import argparse
import hashlib
import json
import random
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path
from time import perf_counter
import unicodedata

BASE = 'Qwen/Qwen3-1.7B'
REVISION = '70d244cc86ccca08cf5af4e1e306ecf908b1ad5e'
PROMPT = 'Extract a cash record as JSON with kind, direction, amount (decimal taka string), currency, date and vendor. /no_think'
ROOT = Path(__file__).resolve().parents[2]
SEED = 621123
FORMATS = (
    ('outflow', 'Lumen Supply', 'Paid {party} {amount} for packing'),
    ('inflow', 'Rafi Print', 'Money received from {party}, {amount}'),
    ('inflow', 'Juniper Textiles', '{party} paid me {amount} for samples'),
    ('outflow', 'Bright Market', 'Spent {amount}k at {party}'),
    ('outflow', 'আঁচল', 'আমি {party}কে {amount} দিলাম'),
    ('inflow', 'জবা', '{party} থেকে {amount} পেলাম'),
    ('outflow', 'চিত্রা প্রেস', '{party}কে {amount} হাজার টাকা দিয়েছি'),
    ('inflow', 'তুলি', '{party} থেকে আজ {amount} নিয়েছি'),
    ('outflow', 'Titas Paper', '{party} ke {amount} disi aj'),
    ('inflow', 'Dheu Store', 'ami {party} theke {amount} tk niyesi'),
    ('outflow', 'Tara Stationery', 'aj {party} ke {amount} hajar diyechi'),
    ('inflow', 'Oikko', '{party} theke {amount} lakh pelam'),
)
ABSTAIN = (
    'I might pay {amount} tomorrow.', 'Did I already receive {amount}?',
    'Received either {amount} or 10 from Rony.', 'I paid EUR {amount} for materials.',
    'আমি {amount} টাকা দিইনি।', 'আজ কি {amount} টাকা পেলাম?',
    'কাল হয়তো {amount} টাকা পাবো।', '{amount} টাকার বিল বাকি আছে।',
    'Tonu ke {amount} diyechi na', 'ami {amount} pailam naki?',
    'about {amount} taka disi', 'aj {amount} dollar pailam',
)
TO_BANGLA = str.maketrans('0123456789', '০১২৩৪৫৬৭৮৯')


def cash_training():
    rng = random.Random(SEED)
    rows = []
    for direction, party, template in FORMATS:
        factor = 100000 if 'lakh' in template else 1000 if any(unit in template for unit in ('}k', 'hajar', 'হাজার')) else 1
        for _ in range(11):
            value = Decimal(rng.randrange(10, 9999)) / (100 if factor > 1 else 1)
            amount = format(value, 'f')
            if any('\u0980' <= c <= '\u09ff' for c in party):
                amount = amount.translate(TO_BANGLA)
            text = template.format(party=party, amount=amount)
            rows.append((text, dict(kind='cash', direction=direction, amount=format(value * factor, 'f'),
                                    currency='BDT', date=None, vendor=party)))
    for template in ABSTAIN:
        for _ in range(5):
            rows.append((template.format(amount=rng.randrange(31, 10000)), {'kind': 'none'}))
    # Interleave examples from the original training split to preserve explicit dates.
    from ml.hisab_lm.train import split_rows
    original = [row for row in split_rows(ROOT / 'web/public/datasets/hisab-lm-synthetic-v1.zip')['train']
                if row['task'] == 'extract_text']
    rows.extend((row['messages'][1]['content'], json.loads(row['messages'][2]['content']))
                for row in rng.sample(original, 48))
    rng.shuffle(rows)
    return rows


def prompt(text):
    return [{'role': 'system', 'content': PROMPT}, {'role': 'user', 'content': text}]


def prediction_score(case, result):
    valid = 'direction' in case
    if not valid:
        return (result.get('kind') == 'none', result.get('kind') == 'cash')
    try:
        amount_ok = Decimal(str(result.get('amount'))) == Decimal(case['amount'])
    except InvalidOperation:
        amount_ok = False
    normalize = lambda value: unicodedata.normalize('NFC', str(value or '')).strip().casefold()
    correct = (result.get('kind') == 'cash' and result.get('currency') == 'BDT' and amount_ok
               and result.get('direction') == case['direction']
               and normalize(result.get('vendor')) == normalize(case['vendor'])
               and (not case.get('date') or result.get('date') == case['date']))
    return (correct, False)


def evaluate(torch, model, tokenizer, cases):
    model.eval()
    tokenizer.padding_side = 'left'
    correct, false_accepts, misses = Counter(), Counter(), []
    with torch.inference_mode():
        for offset in range(0, len(cases), 6):
            batch = cases[offset:offset + 6]
            inputs = tokenizer.apply_chat_template([prompt(case['text']) for case in batch], tokenize=True,
                enable_thinking=False, add_generation_prompt=True, padding=True, return_dict=True, return_tensors='pt').to('cuda')
            output = model.generate(**inputs, do_sample=False, max_new_tokens=110, pad_token_id=tokenizer.pad_token_id)
            for case, answer in zip(batch, tokenizer.batch_decode(output[:, inputs['input_ids'].shape[1]:], skip_special_tokens=True)):
                try:
                    result = json.loads(answer)
                except ValueError:
                    result = None
                matched, unsafe = prediction_score(case, result if isinstance(result, dict) else {})
                correct[case['lang']] += matched
                false_accepts[case['lang']] += unsafe
                if not matched:
                    misses.append(case['id'])
    return dict(exact_by_language=dict(correct), unsafe_cash_by_language=dict(false_accepts), missed=misses)


def train(torch, model, tokenizer, rows):
    model.train()
    params = [parameter for parameter in model.parameters() if parameter.requires_grad]
    optimizer = torch.optim.AdamW(params, lr=7e-5)
    sequences = []
    for text, response in rows:
        prefix = tokenizer.apply_chat_template(prompt(text), tokenize=True, add_generation_prompt=True,
                                               enable_thinking=False, return_dict=False)
        suffix = tokenizer.encode(json.dumps(response, ensure_ascii=False, separators=(',', ':')), add_special_tokens=False)
        suffix.append(tokenizer.eos_token_id)
        sequences.append((prefix + suffix, [-100] * len(prefix) + suffix))
    losses = []
    for offset in range(0, len(sequences), 4):
        batch = sequences[offset:offset + 4]
        length = max(len(tokens) for tokens, _ in batch)
        input_ids = torch.tensor([tokens + [tokenizer.pad_token_id] * (length - len(tokens)) for tokens, _ in batch], device='cuda')
        labels = torch.tensor([labels + [-100] * (length - len(labels)) for _, labels in batch], device='cuda')
        attention = torch.tensor([[1] * len(tokens) + [0] * (length - len(tokens)) for tokens, _ in batch], device='cuda')
        optimizer.zero_grad(set_to_none=True)
        loss = model(input_ids=input_ids, labels=labels, attention_mask=attention, use_cache=False).loss
        loss.backward()
        torch.nn.utils.clip_grad_norm_(params, 1.0)
        optimizer.step()
        losses.append(float(loss.detach()))
        if len(losses) % 15 == 0:
            print(f'{len(losses)} updates; recent training loss {sum(losses[-15:]) / 15:.4f}', flush=True)
    return len(losses)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adapter', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not (args.adapter / 'adapter_model.safetensors').is_file():
        parser.error('Supply a local copy of the released safetensors adapter')
    args.output.mkdir(parents=True, exist_ok=True)
    if any(args.output.iterdir()):
        parser.error('Choose an empty output directory to preserve existing results')
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    if not torch.cuda.is_available():
        raise SystemExit('This pilot requires a CUDA GPU')
    torch.manual_seed(SEED)
    started = perf_counter()
    challenge = (ROOT / 'evals/fixtures/hisab_cash_challenge.jsonl').read_bytes()
    cases = [json.loads(line) for line in challenge.splitlines()]
    rows = cash_training()
    if {text.casefold() for text, _ in rows} & {case['text'].casefold() for case in cases}:
        raise ValueError('Challenge overlaps with training')
    tokenizer = AutoTokenizer.from_pretrained(BASE, revision=REVISION, local_files_only=True, trust_remote_code=False)
    base = AutoModelForCausalLM.from_pretrained(BASE, revision=REVISION, dtype=torch.bfloat16, use_safetensors=True,
                                               local_files_only=True, trust_remote_code=False).to('cuda')
    model = PeftModel.from_pretrained(base, args.adapter, is_trainable=True)
    baseline = evaluate(torch, model, tokenizer, cases)
    print('Baseline:', json.dumps(baseline), flush=True)
    updates = train(torch, model, tokenizer, rows)
    candidate = evaluate(torch, model, tokenizer, cases)
    output = args.output / 'adapter'
    model.save_pretrained(output, safe_serialization=True)
    tokenizer.save_pretrained(output)
    report = dict(base=BASE, base_revision=REVISION, seed=SEED, train_count=len(rows), updates=updates,
        evaluation='engineered synthetic challenge only', sample_count=len(cases), challenge_sha256=hashlib.sha256(challenge).hexdigest(),
        training_sha256=hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest(),
        baseline_sha256=hashlib.sha256((args.adapter / 'adapter_model.safetensors').read_bytes()).hexdigest(),
        candidate_sha256=hashlib.sha256((output / 'adapter_model.safetensors').read_bytes()).hexdigest(),
        baseline=baseline, candidate=candidate, elapsed_seconds=round(perf_counter() - started, 1))
    (args.output / 'scorecard.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
