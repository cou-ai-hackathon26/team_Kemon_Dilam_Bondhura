# Free AI keys for chat

Chat tries Capzoryx's own model first, then each free API below whose key is set on Render
(capzoryx-staging-api → Environment). When the own model answers, the first free API that responds rewrites the
reply into one voice; the rewrite may not add an amount, so figures always come from the books. Owner figures are
sent to these APIs only when the owner allows cloud answers.

More keys mean fewer "busy" moments: each free tier has its own daily limit, and a provider that hits its limit
rests for 10 minutes while the next one answers.

| Render variable | Where to get it (free) | Notes |
| --- | --- | --- |
| `CAPZORYX_CHAT_GROQ_KEY` | console.groq.com → API Keys | Fastest; also used for web research. |
| `CAPZORYX_CHAT_GEMINI_KEY` | aistudio.google.com → Get API key | Falls back to `SAFELEDGER_LLM_API_KEY`. |
| `CAPZORYX_CHAT_CEREBRAS_KEY` | cloud.cerebras.ai → API Keys | Very fast, generous daily tokens. |
| `CAPZORYX_CHAT_OPENROUTER_KEY` | openrouter.ai → Keys | Uses `:free` models only. |
| `CAPZORYX_CHAT_MISTRAL_KEY` | console.mistral.ai → API Keys (Experiment plan) | Free plan needs phone verification. |
| `CAPZORYX_CHAT_GITHUB_KEY` | github.com → Settings → Developer settings → Fine-grained token with "Models: read" | Free with any GitHub account. |
| `CAPZORYX_CHAT_SAMBANOVA_KEY` | cloud.sambanova.ai → API Keys | Free tier. |

Model names on free tiers change often. If a model is retired, the server reads the provider's model list and
switches to the next model it knows, so no change is needed. To pin a model, set `CAPZORYX_CHAT_<NAME>_MODEL`.

Own model: `CAPZORYX_CHAT_HISAB_URL`, `CAPZORYX_CHAT_HISAB_MODE=zerogpu`, and a Hugging Face token in
`CAPZORYX_CHAT_HISAB_KEY` (or the existing `CAPZORYX_HISAB_HF_TOKEN`).
