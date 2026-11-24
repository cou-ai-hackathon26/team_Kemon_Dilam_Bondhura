"""Private inference for typed cash records using the released Hisab-LM adapter."""
import json
from pathlib import Path

import gradio as gr
import spaces
import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

BASE = "Qwen/Qwen3-1.7B"
REVISION = "70d244cc86ccca08cf5af4e1e306ecf908b1ad5e"
ADAPTER = Path(__file__).with_name("adapter")
PROMPT = (
    "Extract a cash record as JSON with kind, direction, amount (decimal taka string), "
    "currency, date and vendor. /no_think"
)
tokenizer = AutoTokenizer.from_pretrained(BASE, revision=REVISION, trust_remote_code=False)
base = AutoModelForCausalLM.from_pretrained(
    BASE, revision=REVISION, dtype=torch.bfloat16, use_safetensors=True, trust_remote_code=False
)
model = PeftModel.from_pretrained(base, ADAPTER, is_trainable=False, torch_device="cpu").merge_and_unload().to("cuda").eval()


@spaces.GPU(duration=15)
def extract(text: str):
    text = text.strip()
    if not 3 <= len(text) <= 400:
        raise gr.Error("Enter one cash transaction in 400 characters or fewer.")
    messages = [{"role": "system", "content": PROMPT}, {"role": "user", "content": text}]
    inputs = tokenizer.apply_chat_template(
        messages, add_generation_prompt=True, enable_thinking=False, tokenize=True, return_dict=True, return_tensors="pt"
    ).to(model.device)
    with torch.inference_mode():
        tokens = model.generate(**inputs, do_sample=False, max_new_tokens=125)
    answer = tokenizer.decode(tokens[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
    try:
        result = json.loads(answer)
    except ValueError:
        return {"kind": "none"}
    return result if isinstance(result, dict) else {"kind": "none"}


with gr.Blocks(analytics_enabled=False) as app:
    sentence = gr.Textbox(label="Cash record", max_length=400)
    result = gr.JSON(label="Draft")
    gr.Button("Extract", variant="primary").click(
        extract, sentence, result, api_name="extract", concurrency_limit=1
    )
app.queue(max_size=4).launch()
