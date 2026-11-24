"""Hisab-Chat on a Hugging Face ZeroGPU Space.

Capzoryx calls the `chat` API with the same messages it sends any provider (system prompt with the business facts,
recent turns, the question) and gets the reply text back. A GPU is attached only while a reply is generated.
Built on the same pattern and library versions as the working `capzoryx-hisab-inference` Space.
"""
import json
import os

import gradio as gr
import spaces
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

REPO = os.environ.get('MODEL_REPO', 'Pranto-404/hisab-chat-merged')
TOKEN = os.environ.get('HF_TOKEN')
GPU_SECONDS = int(os.environ.get('GPU_SECONDS', '20'))  # a short reservation leaves more of the daily quota
tokenizer = AutoTokenizer.from_pretrained(REPO, token=TOKEN)
model = AutoModelForCausalLM.from_pretrained(REPO, token=TOKEN, dtype=torch.bfloat16, use_safetensors=True).to('cuda').eval()


@spaces.GPU(duration=GPU_SECONDS)
def chat(messages_json: str, max_tokens: float = 500):
    try:
        messages = [m for m in json.loads(messages_json) if m.get('role') in ('system', 'user', 'assistant')][-14:]
    except (ValueError, AttributeError):
        raise gr.Error('Send a JSON list of {role, content} messages.')
    if not messages or messages[-1]['role'] != 'user':
        raise gr.Error('The last message must be from the user.')
    inputs = tokenizer.apply_chat_template(messages, add_generation_prompt=True, enable_thinking=False, tokenize=True,
                                           return_dict=True, return_tensors='pt').to(model.device)
    with torch.inference_mode():
        tokens = model.generate(**inputs, max_new_tokens=max(32, min(int(max_tokens), 600)), do_sample=True, temperature=0.6,
                                top_p=0.9, repetition_penalty=1.05, pad_token_id=tokenizer.eos_token_id)
    return tokenizer.decode(tokens[0][inputs['input_ids'].shape[1]:], skip_special_tokens=True).strip()


with gr.Blocks(analytics_enabled=False) as app:
    messages = gr.Textbox(label='Messages (JSON list of {role, content})', lines=6)
    budget = gr.Number(value=500, label='Max new tokens')
    reply = gr.Textbox(label='Reply')
    gr.Button('Answer', variant='primary').click(chat, [messages, budget], reply, api_name='chat', concurrency_limit=2)
app.queue(max_size=16).launch()
