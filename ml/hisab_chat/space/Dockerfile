# Free CPU Hugging Face Space that serves Hisab-Chat with an OpenAI-compatible API at /v1.
# Space secrets: HF_TOKEN (read access to the private model repo) and LLAMA_API_KEY (any long random string).
FROM ghcr.io/ggml-org/llama.cpp:server
ENV LLAMA_ARG_HOST=0.0.0.0 \
    LLAMA_ARG_PORT=7860 \
    LLAMA_ARG_CTX_SIZE=4096 \
    LLAMA_ARG_N_PARALLEL=2 \
    LLAMA_ARG_ALIAS=hisab-chat \
    LLAMA_ARG_HF_REPO=Pranto-404/hisab-chat-gguf \
    LLAMA_ARG_HF_FILE=hisab-chat-q4_k_m.gguf \
    LLAMA_CACHE=/tmp/llama-cache
EXPOSE 7860
