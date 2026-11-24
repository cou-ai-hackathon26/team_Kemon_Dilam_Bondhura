"""Answers to open business questions written by the base model itself, for Hisab-Chat training.

A model fine-tuned mostly on short template replies forgets how to answer anything new (the first L4 run replied to
"how do I sell more before Eid?" with a sentence about PINs). Mixing in the base model's own answers to open
questions, in the exact production format, keeps its general ability while it learns Capzoryx's shape. Answers are
kept only when every amount appears in the facts and the reply is in the question's language.

    python -m ml.hisab_chat.self_teach --out data/selfteach.jsonl --count 600 --base Qwen/Qwen3-4B-Instruct-2507
"""
import argparse
import json
import random
from pathlib import Path

from api.app.chat import context, llm
from api.app.chat.understand import language
from ml.hisab_chat import business_data

EXTRA_Q = {
    'en': ['How can I get more repeat customers?', 'Should I offer home delivery?', 'How do I plan stock for the next month?',
           'My sales are slow this week. What should I check first?', 'How should I price a new product?',
           'Is it worth selling on Daraz or Facebook?', 'How can I reduce wastage?', 'Should I take a small loan to buy stock?'],
    'bl': ['kivabe purono customer der abar anbo?', 'home delivery dile ki labh hobe?', 'porer masher stock kivabe plan korbo?',
           'ei soptahe bikri kom, age ki dekhbo?', 'notun product er dam kivabe thik korbo?', 'daraz naki facebook e bikri korbo?',
           'nosto howa kivabe komabo?', 'stock kinte choto loan neya ki thik hobe?'],
    'bn': ['পুরোনো ক্রেতাদের কীভাবে আবার আনব?', 'হোম ডেলিভারি দিলে কী লাভ হবে?', 'পরের মাসের স্টক কীভাবে পরিকল্পনা করব?',
           'এই সপ্তাহে বিক্রি কম, আগে কী দেখব?', 'নতুন পণ্যের দাম কীভাবে ঠিক করব?', 'স্টক কিনতে ছোট ঋণ নেওয়া কি ঠিক হবে?'],
}


def prompts(count, seed):
    rng = random.Random(seed)
    out = []
    for _ in range(count):
        ctx, _ = business_data.invent(rng)
        lang = rng.choice(('bl', 'bl', 'bn', 'en'))
        question = rng.choice(business_data.OPEN_Q[lang] + EXTRA_Q[lang])
        out.append((lang, question, context.render(ctx), business_data.system(ctx)))
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--count', type=int, default=1200)
    p.add_argument('--base', default='Qwen/Qwen3-4B-Instruct-2507')
    p.add_argument('--batch', type=int, default=24)
    p.add_argument('--seed', type=int, default=4242)
    a = p.parse_args()
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(a.base)
    tokenizer.padding_side = 'left'
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    try:
        model = AutoModelForCausalLM.from_pretrained(a.base, dtype=dtype).to('cuda').eval()
    except TypeError:
        model = AutoModelForCausalLM.from_pretrained(a.base, torch_dtype=dtype).to('cuda').eval()
    items, kept, rows = prompts(a.count, a.seed), 0, []
    a.out.parent.mkdir(parents=True, exist_ok=True)
    sink = a.out.open('w')  # written after every batch, so a stopped run still leaves the answers it kept
    for start in range(0, len(items), a.batch):
        batch = items[start:start + a.batch]
        texts = []
        for _, question, _, system in batch:
            messages = [{'role': 'system', 'content': system}, {'role': 'user', 'content': question}]
            try:
                texts.append(tokenizer.apply_chat_template(messages, add_generation_prompt=True, tokenize=False, enable_thinking=False))
            except TypeError:
                texts.append(tokenizer.apply_chat_template(messages, add_generation_prompt=True, tokenize=False))
        enc = tokenizer(texts, return_tensors='pt', padding=True).to('cuda')
        with torch.inference_mode():
            out = model.generate(**enc, max_new_tokens=380, do_sample=True, temperature=0.7, top_p=0.9,
                                 pad_token_id=tokenizer.pad_token_id)
        for (lang, question, facts, system), tokens in zip(batch, out):
            answer = llm.clean(tokenizer.decode(tokens[enc['input_ids'].shape[1]:], skip_special_tokens=True))
            if len(answer) < 80 or not llm.grounded(answer, facts, question):
                continue
            if lang != 'en' and language(answer, answer.casefold().split()) != lang:
                continue
            rows.append({'source': 'synthetic-business-selfteach', 'messages': [
                {'role': 'system', 'content': system}, {'role': 'user', 'content': question}, {'role': 'assistant', 'content': answer}]})
            sink.write(json.dumps(rows[-1], ensure_ascii=False) + '\n')
            kept += 1
        sink.flush()
        print(f'self-teach: {min(start + a.batch, len(items))}/{len(items)} prompts, {kept} answers kept', flush=True)
    sink.close()
    if rows:
        print('example:', rows[0]['messages'][1]['content'], '->', rows[0]['messages'][2]['content'][:400], flush=True)
    del model
    torch.cuda.empty_cache()


if __name__ == '__main__':
    main()
