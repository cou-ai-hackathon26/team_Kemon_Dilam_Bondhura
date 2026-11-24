"""Score a served chat model on the frozen validation split.

    python -m ml.hisab_chat.eval --data data/hisab_chat --url http://127.0.0.1:8080/v1 --model hisab-chat
    python -m ml.hisab_chat.eval --data data/hisab_chat --url https://api.groq.com/openai/v1 --model llama-3.3-70b-versatile --key $KEY

Metrics: grounded (no invented amount), fact recall (the expected amount appears when the reference states one),
language match, and latency. Run it for Hisab-Chat and for a free API model to compare them on identical inputs.
"""
import argparse
import json
import statistics
import time
from pathlib import Path

import httpx

from api.app.chat.llm import clean, grounded, numbers
from api.app.chat.understand import language


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--data', type=Path, required=True)
    p.add_argument('--url', required=True)
    p.add_argument('--model', required=True)
    p.add_argument('--key', default='')
    p.add_argument('--limit', type=int, default=300)
    a = p.parse_args()
    rows = [json.loads(line) for line in (a.data / 'validation.jsonl').read_text().splitlines()][:a.limit]
    scores = dict(grounded=0, recall=0, recall_total=0, language=0, failed=0)
    latency = []
    headers = {'Authorization': 'Bearer ' + a.key} if a.key else {}
    with httpx.Client(timeout=60, trust_env=False) as client:
        for row in rows:
            messages = row['messages']
            reference = messages[-1]['content']
            prompt, facts, question = messages[:-1], messages[0]['content'], messages[-2]['content']
            start = time.monotonic()
            try:
                reply = client.post(a.url.rstrip('/') + '/chat/completions', headers=headers, json={
                    'model': a.model, 'messages': prompt, 'max_tokens': 300, 'temperature': 0.2}).json()
                answer = clean(reply['choices'][0]['message']['content'] or '')
            except (httpx.HTTPError, KeyError, IndexError, ValueError):
                scores['failed'] += 1
                continue
            latency.append(time.monotonic() - start)
            scores['grounded'] += grounded(answer, facts, question)
            expected = numbers(reference)
            if expected:
                scores['recall_total'] += 1
                scores['recall'] += expected <= numbers(answer)
            asked = language(question, question.casefold().split())
            scores['language'] += asked == 'en' or language(answer, answer.casefold().split()) == asked
    done = max(1, len(rows) - scores['failed'])
    report = dict(model=a.model, cases=len(rows), failed=scores['failed'],
                  grounded=round(scores['grounded'] / done, 4),
                  fact_recall=round(scores['recall'] / max(1, scores['recall_total']), 4),
                  language_match=round(scores['language'] / done, 4),
                  latency_p50=round(statistics.median(latency), 2) if latency else None,
                  latency_p95=round(sorted(latency)[int(len(latency) * 0.95) - 1], 2) if latency else None)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
