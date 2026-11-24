---
title: Hisab-Chat
sdk: docker
app_port: 7860
pinned: false
---

Serves Capzoryx's own Hisab-Chat model with llama.cpp on a free CPU Space. Set the Space secrets `HF_TOKEN`
(read access to `Pranto-404/hisab-chat-gguf`) and `LLAMA_API_KEY`. The bot calls
`https://pranto-404-hisab-chat.hf.space/v1` with that key.
