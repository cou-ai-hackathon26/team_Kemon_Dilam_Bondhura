# Hisab-Chat: train and serve Capzoryx's own chat model on one RTX 5060 Ti

Hisab-Chat is the first model the Telegram and in-app chat try (`api/app/chat/llm.py`). Free API tiers
are only the fallback. Everything below is free: open models, your own GPU and a free Cloudflare tunnel.

## Competition in the next 24 hours, from a normal laptop

Nothing runs on the laptop. Kaggle trains the model for free, and Hugging Face ZeroGPU serves it.

1. **Tokens (10 min):**
   - Hugging Face write token.
   - A GitHub token with `models:read` (github.com → Settings → Developer settings → Fine-grained token → Account permissions → Models: Read). With Student/Copilot Pro the GitHub Models limits are higher.
   - Add both to Kaggle secrets as `HF_TOKEN` and `GITHUB_KEY`, next to `GROQ_KEY` / `GEMINI_KEY` / `CEREBRAS_KEY` / `OPENROUTER_KEY`.
2. **Train (about 2 hours, unattended):** Kaggle, GPU T4 x2, Internet on. Run with Save Version → Save & Run All:

   ```python
   !rm -rf Capzoryx && git clone -q https://github.com/Pranto-404/Capzoryx
   %env HISAB_TARGET=zerogpu
   %run Capzoryx/ml/hisab_chat/kaggle_run.py
   ```

   It fine-tunes Qwen3-4B-Instruct-2507 on about 4,100 conversations (QLoRA, one epoch), uploads the merged weights privately and starts the ZeroGPU Space.
3. **Render (5 min):**
   - Add the three values the run prints: `CAPZORYX_CHAT_HISAB_URL`, `_MODE`, `_KEY`.
   - Add `CAPZORYX_CHAT_GITHUB_KEY` (the GitHub token) as a strong free backup.
4. **Keep it ready:**
   - Add the Space URL to UptimeRobot.
   - Rehearse once, then avoid using up the day's GPU quota before the demo. The quota resets 24 hours after first use.

Order at run time:
1. Code answers exact questions.
2. Hisab-Chat on ZeroGPU answers open ones.
3. When quota or the Space is unavailable, the free APIs answer: GitHub Models, Groq, Gemini and others.

## Free plan (recommended): one model, three free layers

1. **Code first.** Exact business questions are answered by code, with no model:
   - balance and bills
   - profile, products, market and competitors
   - risk, break-even and price scenarios
2. **Hisab-Chat on ZeroGPU.** Open questions go here first. The default base is Qwen3-1.7B, which answers in 1-3 GPU seconds, so even a free account's daily ZeroGPU minutes cover many replies.
3. **The same model as GGUF on a free CPU Space.** Used when the GPU quota is spent. It gets the most important part of the facts so a 2-core CPU answers in about 20-30 seconds.
4. **Free APIs.** Used only when both copies fail or answer without grounding.

Run the Kaggle cell with `%env HISAB_TARGET=zerogpu` and `%env HISAB_BASE=Qwen/Qwen3-1.7B`. It trains Qwen3-1.7B (about 40 minutes on a T4) and starts both Spaces. It then prints five Render variables:
- `CAPZORYX_CHAT_HISAB_URL`, `_MODE` and `_KEY`
- `CAPZORYX_CHAT_HISAB_CPU_URL` and `_CPU_KEY`

Without `HISAB_BASE` the run uses Qwen3-4B on ZeroGPU only: better Bangla, no CPU copy.

Keep both Spaces awake: add their URLs to UptimeRobot (HTTP(s) monitor, every 30 minutes). A free Space otherwise sleeps after two days without requests.

## Fastest path for a demo: train in about 2 hours, serve on ZeroGPU

**What the model learns.** It does not learn the owner's numbers; those arrive in every request as a facts block
built by `api/app/chat/context.py`, covering:
- the business profile
- each product's cost, market price and break-even
- growth
- market prices and cited research
- alerts and advisor findings

The model learns three things: to read that block, to answer in Bangla, Banglish or English with the shape the
app uses, and to say "not known" instead of inventing. Training data uses the same `render` as production:
- `business_data.py` invents businesses from every industry and computes their figures with the real code.
- Teacher replies are kept only when every amount appears in the facts.
- Price-move scenarios stay in code and are never taught.

**Run it (Kaggle, GPU T4 x2, Internet on).** You need a Hugging Face **PRO** account to host ZeroGPU.
1. Add the secrets: `HF_TOKEN` (write), plus free `GROQ_KEY` / `GEMINI_KEY` / `CEREBRAS_KEY` / `OPENROUTER_KEY`.
2. Run one cell:

   ```python
   !rm -rf Capzoryx && git clone -q https://github.com/Pranto-404/Capzoryx
   %env HISAB_TARGET=zerogpu
   %run Capzoryx/ml/hisab_chat/kaggle_run.py
   ```

   About 1-2 hours later the model has been fine-tuned (one epoch) on about 6,900 conversations. The run uploads:
   - merged weights to `<you>/hisab-chat-merged`
   - a GGUF to `<you>/hisab-chat-gguf`
   - a private Gradio Space `<you>/hisab-chat-zero` on ZeroGPU
3. On Render set:
   - `CAPZORYX_CHAT_HISAB_URL=https://<you>-hisab-chat-zero.hf.space`
   - `CAPZORYX_CHAT_HISAB_MODE=zerogpu`
   - `CAPZORYX_CHAT_HISAB_KEY=<a READ token from the PRO account>`

**How it behaves in production.**
- Business questions with exact answers (profile, products, market, risk, break-even, price scenarios) are answered by code, with no model at all.
- Open questions go to Hisab-Chat first. The free APIs are used only if the Space is busy, out of GPU quota or gives an ungrounded answer.
- ZeroGPU minutes are counted on the token's account: [about 25 minutes a day on PRO](https://huggingface.co/docs/hub/spaces-zerogpu), roughly a few hundred replies.
- Without PRO, leave `HISAB_TARGET` unset. You get the free CPU Space with the GGUF, which is slower but free.

## No PC needed: one Kaggle cell trains, publishes and starts the model

Nothing is installed on your computer, and it keeps running with the browser closed.

1. **Hugging Face token**: huggingface.co → Settings → Access Tokens → New token, type **Write**.
2. **Kaggle**: kaggle.com → Create → New Notebook (keep it private). Settings → Accelerator **GPU T4 x2**,
   Internet **on**. Add-ons → Secrets: `HF_TOKEN`, plus any of `GROQ_KEY`, `GEMINI_KEY`, `CEREBRAS_KEY`,
   `OPENROUTER_KEY` (free keys; they write the natural conversations within their daily budgets).
3. Paste one cell and press **Save Version → Save & Run All**:

   ```python
   !rm -rf Capzoryx && git clone -q https://github.com/Pranto-404/Capzoryx
   %run Capzoryx/ml/hisab_chat/kaggle_run.py
   ```

   About 1–1.5 hours later the log ends with two values. It has built the data, trained Hisab-Chat (one epoch),
   uploaded it privately to `Pranto-404/hisab-chat-gguf`, and created the `Pranto-404/hisab-chat` Space that serves it.
4. **Render** → `capzoryx-staging-api` → Environment: add `CAPZORYX_CHAT_HISAB_URL` and `CAPZORYX_CHAT_HISAB_KEY`
   with those two values. The bot then asks Hisab-Chat first.

A free Space sleeps after two days without requests and takes a minute to wake; during that minute the free API
fallbacks answer.

## With your own GPU (RTX 5060 Ti)

### 1. Set up (Windows: use WSL2 Ubuntu)

```bash
python3.12 -m venv ~/hisab && source ~/hisab/bin/activate
pip install torch --index-url https://download.pytorch.org/whl/cu128   # RTX 50-series needs CUDA 12.8+
pip install -r ml/hisab_lm/requirements-gpu.lock.txt                   # unsloth, transformers, peft, datasets
pip install -r api/requirements.lock.txt
nvidia-smi                                                             # the 5060 Ti should be listed
git clone https://github.com/ggml-org/llama.cpp && cmake -S llama.cpp -B llama.cpp/build -DGGML_CUDA=ON \
  && cmake --build llama.cpp/build -j --target llama-server && export PATH=$PWD/llama.cpp/build/bin:$PATH
```

### 2. Full-speed pipeline on a 16 GB card

The GPU does the bulk work; free API teachers are only used for grading, within a daily budget that leaves 30% of
each quota for the live bot (`teachers.py`, usage kept in `~/.cache/capzoryx/teacher_usage.json`).

**A. Teacher on the GPU writes natural conversations (~30–40 min).** Qwen3-30B-A3B is a mixture-of-experts model:
30B parameters of knowledge, but only 3B work on each word, so it runs about as fast as a small model. In 4-bit it
needs ~19 GB, so `--n-cpu-moe` keeps some expert layers in system RAM (32 GB is enough) and the rest on the GPU.
With 16 GB of RAM use `ggml-org/gemma-3-12b-it-GGUF:Q4_K_M` without `--n-cpu-moe` instead.

```bash
TEACHER=unsloth/Qwen3-30B-A3B-Instruct-2507-GGUF:Q4_K_M
llama-server -hf $TEACHER --port 8081 -ngl 99 --n-cpu-moe 16 -c 16384 -np 8 -fa on &
python -m ml.hisab_chat.build_dataset --out data/hisab_chat --size 6000 --teacher 3000 \
    --local-teacher-url http://127.0.0.1:8081/v1 --workers 8
```

**B. Train (~30–45 min).** Stop the teacher first (`kill %1`) so training has the whole card. On 16 GB the 4B model
trains in 16-bit (faster and more accurate than 4-bit); add `--qlora` only if it runs out of memory.

```bash
python -m ml.hisab_chat.train --data data/hisab_chat --output runs/hisab-chat --gguf
```

**C. Self-play round (~20–30 min).** Student and teacher share the card:

```bash
llama-server -m runs/hisab-chat/gguf/hisab-chat-q4_k_m.gguf --port 8080 -ngl 99 -c 8192 -np 8 --api-key LOCAL &
llama-server -hf $TEACHER --port 8081 -ngl 99 --n-cpu-moe 26 -c 16384 -np 8 -fa on &   # more experts in RAM: the student uses part of the GPU
python -m ml.hisab_chat.self_play --student-url http://127.0.0.1:8080/v1 --student-key LOCAL \
    --local-teacher-url http://127.0.0.1:8081/v1 --examples 600 --workers 8 --minutes 40 \
    --out data/hisab_chat/selfplay-r1.jsonl
```

The GPU teacher writes the test questions in twelve styles (typos, slang, formal, follow-ups, unsafe and off-topic).
Grading and rewrites go to the strongest teacher with budget left: Groq, Cerebras, Mistral, OpenRouter, Gemini or
GitHub Models when their `CAPZORYX_CHAT_*_KEY` is set, otherwise the GPU teacher. Rewrites that state an amount not in
the facts, or answer in the wrong language, are dropped. `selfplay-r1.report.json` records the mean score and the API
calls spent today.

**D. Merge and retrain (~40 min), then publish.**

```bash
kill %1 %2
python -m ml.hisab_chat.build_dataset --out data/hisab_chat --size 6000 --teacher 3000 \
    --local-teacher-url http://127.0.0.1:8081/v1 --extra data/hisab_chat/selfplay-r1.jsonl   # restart the step-A teacher first
python -m ml.hisab_chat.train --data data/hisab_chat --output runs/hisab-chat-r2 --gguf --push Pranto-404/hisab-chat-gguf
```

Repeat C–D (`-r2`, `-r3`) while the mean score keeps rising. One full round takes about 2–2.5 hours.

If llama-server reports it is out of memory, raise `--n-cpu-moe` by 4 and start it again; if the GPU sits below
~90% in `nvidia-smi` and memory is free, lower it by 4 for more speed.

### 3. Measure before you claim

```bash
llama-server -m runs/hisab-chat/gguf/*.gguf --port 8080 -ngl 99 -c 4096 --api-key YOUR_LOCAL_KEY &
python -m ml.hisab_chat.eval --data data/hisab_chat --url http://127.0.0.1:8080/v1 --model hisab-chat --key YOUR_LOCAL_KEY
python -m ml.hisab_chat.eval --data data/hisab_chat --url https://api.groq.com/openai/v1 --model llama-3.3-70b-versatile --key $CAPZORYX_CHAT_GROQ_KEY
```

Report `grounded` (no invented amounts), `fact_recall`, `language_match` and latency side by side. Before you
present them, add a hand-written test set from real speakers; the bundled validation split is synthetic.

### 4. Serve it to the live bot

```bash
llama-server -m runs/hisab-chat/gguf/*.gguf --host 127.0.0.1 --port 8080 -ngl 99 -c 4096 --api-key YOUR_LOCAL_KEY
cloudflared tunnel --url http://127.0.0.1:8080          # free; prints https://<name>.trycloudflare.com
```

On the Render API service set `CAPZORYX_CHAT_HISAB_URL=https://<name>.trycloudflare.com/v1` and
`CAPZORYX_CHAT_HISAB_KEY=YOUR_LOCAL_KEY`. When your PC is off, the bot uses the free API fallbacks automatically.
A quick-tunnel address changes on every restart; a free Cloudflare account with a named tunnel keeps it fixed.
