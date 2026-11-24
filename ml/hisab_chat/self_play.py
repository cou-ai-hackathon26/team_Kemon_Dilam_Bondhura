"""Self-play distillation: a large teacher model quizzes Hisab-Chat, grades it and rewrites its weak answers.

One round:
  1. the teacher writes realistic owner messages (Bangla, Banglish, English; typos, slang, formal, follow-ups);
  2. the student (Hisab-Chat, served on an OpenAI-compatible URL) answers each one against synthetic facts;
  3. the teacher scores the answer 1-5 and writes a better one;
  4. the better answer is kept only if every amount matches the facts and it is in the owner's language.
Low-scoring cases become new training rows, so each round trains on what the student got wrong. The report's mean
score is the round's measurable progress.

    python -m ml.hisab_chat.self_play --student-url http://127.0.0.1:8080/v1 --student-key KEY \\
        --examples 600 --out data/hisab_chat/selfplay-r1.jsonl
    python -m ml.hisab_chat.build_dataset --out data/hisab_chat --size 6000 --teacher 2000 \\
        --extra data/hisab_chat/selfplay-r1.jsonl

A local GPU teacher (--local-teacher-url) writes the questions; free API teachers grade and rewrite within their
daily budgets (see teachers.py), and the local teacher takes over when a budget is spent.
"""
import argparse
import json
import random
import statistics
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

import httpx

from api.app.chat import llm
from api.app.chat.understand import language
from ml.hisab_chat.build_dataset import facts, system
from ml.hisab_chat.teachers import Teachers, add_arguments
from api.app.chat.prompts import JUDGE_CRITERIA

TRANSPORT = None  # tests inject an httpx.MockTransport (student calls)
STYLES = ['casual Banglish with spelling mistakes', 'formal Bangla script', 'short English with typos',
          'polite formal English', 'Banglish slang with "vai" or "apu"', 'mixed Bangla script and English words',
          'a follow-up that refers to the previous answer', 'a vague question that needs clarifying',
          'a question about business advice', 'small talk or a greeting', 'an unsafe request (send money, share a PIN)',
          'an off-topic question']
TOPICS = ['cash in hand', 'sales this month', 'spending', 'supplier bills', 'who owes money', 'next week cash',
          'loans', 'pricing', 'stock for Eid', 'collecting dues politely', 'saving money', 'bKash and bank fees']
ASK = ("You write test messages that small shop owners in Bangladesh send to their bookkeeping assistant. Write {n} "
       "different messages in this style: {style}. Topics to mix: {topics}. Make them sound real, not textbook. "
       'Return only JSON: {{"messages": ["...", "..."]}}')
JUDGE = ("You grade a finance assistant for Bangladeshi small businesses. The ideal reply is " + JUDGE_CRITERIA + "."
         "\n\nFacts:\n{facts}\n\nOwner: {question}\nAssistant: {answer}\n\n"
         'Return only JSON: {{"score": 1-5, "problems": "short reason", "better": "the ideal reply"}}')


def student_answer(url, model, key, messages):
    headers = {'Authorization': 'Bearer ' + key} if key else {}
    with httpx.Client(transport=TRANSPORT, trust_env=False, timeout=60) as client:
        reply = client.post(url.rstrip('/') + '/chat/completions', headers=headers,
                            json={'model': model, 'messages': messages, 'max_tokens': 300, 'temperature': 0.2})
    return llm.clean(reply.json()['choices'][0]['message']['content'] or '')


def grade_batch(teachers, student, seed, deadline):
    """One teacher-written batch: (score, training row or None, rejected) for each question."""
    rng = random.Random(seed)
    # Writing questions is bulk work for the GPU teacher; grading and rewriting go to the strongest teacher available.
    batch = teachers.ask_json(ASK.format(n=8, style=rng.choice(STYLES), topics=', '.join(rng.sample(TOPICS, 3))), deadline,
                              prefer='local')
    results = []
    for question in [q for q in (batch or {}).get('messages', []) if isinstance(q, str) and 1 < len(q) <= 300]:
        _, facts_text = facts(rng)
        prompt = [{'role': 'system', 'content': system(facts_text)}, {'role': 'user', 'content': question}]
        try:
            answer = student_answer(*student, prompt)
        except (httpx.HTTPError, ValueError, KeyError, IndexError):
            continue
        verdict = teachers.ask_json(JUDGE.format(facts=facts_text, question=question, answer=answer), deadline,
                                    prefer='remote', max_tokens=500)
        try:
            score = min(5, max(1, int((verdict or {}).get('score'))))
        except (TypeError, ValueError):
            continue
        better = llm.clean(str((verdict or {}).get('better') or ''))
        asked = language(question, question.casefold().split())
        usable = bool(better) and llm.grounded(better, facts_text, question) and (
            asked == 'en' or language(better, better.casefold().split()) == asked)
        row = {'source': 'synthetic-selfplay', 'messages': [*prompt, {'role': 'assistant', 'content': better}]}
        results.append((score, row if usable else None))
    return results


def run(student_url, student_model, student_key, examples, keep_below, *, teachers=None, workers=8, minutes=50, seed=7):
    """Grade `examples` answers with every teacher in parallel; stop early at the time limit."""
    deadline = time.monotonic() + minutes * 60
    teachers, student = teachers or Teachers(), (student_url, student_model, student_key)
    graded, seeds = [], iter(range(seed, seed + 10**6))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        running = {pool.submit(grade_batch, teachers, student, next(seeds), deadline) for _ in range(workers)}
        while running:
            done, running = wait(running, return_when=FIRST_COMPLETED)
            for future in done:
                if len(graded) < examples:
                    graded += future.result()
            if len(graded) < examples and time.monotonic() < deadline:
                running |= {pool.submit(grade_batch, teachers, student, next(seeds), deadline) for _ in done}
    graded = graded[:examples]
    scores = [score for score, _ in graded]
    rows = [row for score, row in graded if score < keep_below and row]
    rejected = sum(1 for score, row in graded if score < keep_below and not row)
    report = dict(graded=len(scores), mean_score=round(statistics.mean(scores), 3) if scores else None,
                  share_good=round(sum(s >= 4 for s in scores) / len(scores), 3) if scores else None,
                  new_training_rows=len(rows), rejected_by_checker=rejected,
                  api_calls_today={k: v for k, v in teachers.usage.items() if k != 'day'})
    return rows, report


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--student-url', required=True)
    p.add_argument('--student-model', default='hisab-chat')
    p.add_argument('--student-key', default='')
    p.add_argument('--examples', type=int, default=600)
    p.add_argument('--keep-below', type=int, default=4, help='keep teacher rewrites for answers scored below this')
    p.add_argument('--workers', type=int, default=8, help='parallel batches (match llama-server --parallel)')
    p.add_argument('--minutes', type=int, default=50, help='stop and save after this long')
    add_arguments(p)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    teachers = Teachers(a.local_teacher_url, a.local_teacher_model, a.local_teacher_key)
    if not teachers.local and not teachers.remote():
        raise SystemExit('Start a local teacher (--local-teacher-url) or set a teacher key, e.g. CAPZORYX_CHAT_GROQ_KEY')
    rows, report = run(a.student_url, a.student_model, a.student_key, a.examples, a.keep_below,
                       teachers=teachers, workers=a.workers, minutes=a.minutes)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))
    a.out.with_suffix('.report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
