"""QLoRA entry point for a free CUDA notebook. Outputs remain on its local disk."""
import argparse
import hashlib
import json
from math import ceil
from pathlib import Path
from zipfile import ZipFile


def split_rows(archive):
    with ZipFile(archive) as z:
        manifest=json.loads(z.read("manifest.json"))
        splits={}
        for name in ("train","validation"):
            raw=z.read(name+".jsonl")
            if hashlib.sha256(raw).hexdigest()!=manifest["splits"][name]["sha256"]:
                raise ValueError(f"{name} digest mismatch")
            rows=[json.loads(line) for line in raw.splitlines()]
            if any(row["source"]!="synthetic" for row in rows):
                raise ValueError("Only verified synthetic examples belong in this training corpus")
            splits[name]=rows
        return splits


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--archive",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--base",choices=("Qwen/Qwen3-1.7B","Qwen/Qwen3-4B","google/gemma-3-4b-it"),required=True)
    p.add_argument("--revision",required=True,help="Exact pinned Hugging Face commit SHA")
    p.add_argument("--epochs",type=int,choices=(2,3),default=2)
    p.add_argument("--gguf",action="store_true")
    a=p.parse_args()
    import re
    if not re.fullmatch(r"[0-9a-f]{40}",a.revision):p.error("Use a full base model revision SHA")
    rows=split_rows(a.archive)
    from unsloth import FastLanguageModel
    from datasets import Dataset
    from huggingface_hub import snapshot_download
    from transformers import Trainer,TrainingArguments
    import torch
    if not torch.cuda.is_available():raise SystemExit("A CUDA GPU is required for this 4-bit training recipe")
    source=snapshot_download(a.base,revision=a.revision)
    model,tokenizer=FastLanguageModel.from_pretrained(model_name=source,max_seq_length=2048,load_in_4bit=True)
    model=FastLanguageModel.get_peft_model(model,r=16,lora_alpha=32,lora_dropout=0.05,
        target_modules=["q_proj","k_proj","v_proj","o_proj","gate_proj","up_proj","down_proj"],
        bias="none",use_gradient_checkpointing="unsloth",random_state=20261121)
    if tokenizer.pad_token_id is None:tokenizer.pad_token=tokenizer.eos_token
    def tokenize(row):
        history=row["messages"][:2]
        if a.base.startswith("google/gemma"):
            history=[{"role":"user","content":history[0]["content"]+"\n"+history[1]["content"]}]
        prompt=tokenizer.apply_chat_template(history,add_generation_prompt=True,tokenize=True,enable_thinking=False,return_dict=False)
        answer=tokenizer.encode(row["messages"][-1]["content"],add_special_tokens=False)+[tokenizer.eos_token_id]
        if len(prompt)+len(answer)>2048:raise ValueError("Training example exceeds 2048 tokens")
        return dict(input_ids=prompt+answer,labels=[-100]*len(prompt)+answer)
    sets={name:Dataset.from_list([tokenize(r) for r in group]) for name,group in rows.items()}
    def collate(batch):
        length=max(len(r["input_ids"]) for r in batch)
        def padded(field,padding):
            return torch.tensor([r[field]+[padding]*(length-len(r[field])) for r in batch])
        return {"input_ids":padded("input_ids",tokenizer.pad_token_id),"labels":padded("labels",-100),
                "attention_mask":torch.tensor([[1]*len(r["input_ids"])+[0]*(length-len(r["input_ids"])) for r in batch])}
    half_epoch=max(1,ceil(len(sets["train"])/16/2))
    params=TrainingArguments(output_dir=str(a.output/"checkpoints"),num_train_epochs=a.epochs,
        per_device_train_batch_size=1,gradient_accumulation_steps=16,per_device_eval_batch_size=1,
        learning_rate=2e-4,lr_scheduler_type="cosine",bf16=torch.cuda.is_bf16_supported(),
        fp16=not torch.cuda.is_bf16_supported(),seed=20261121,report_to="none",push_to_hub=False,
        eval_strategy="steps",save_strategy="steps",eval_steps=half_epoch,save_steps=half_epoch,save_total_limit=2,
        load_best_model_at_end=True,metric_for_best_model="eval_loss",greater_is_better=False)
    trainer=Trainer(model=model,args=params,train_dataset=sets["train"],eval_dataset=sets["validation"],data_collator=collate)
    trainer.train()
    dest=str(a.output/"adapter")
    model.save_pretrained(dest);tokenizer.save_pretrained(dest)
    if a.gguf:model.save_pretrained_gguf(str(a.output/"gguf"),tokenizer,quantization_method="q4_k_m")
    evidence={"base":a.base,"base_revision":a.revision,"archive_sha256":hashlib.sha256(a.archive.read_bytes()).hexdigest(),
              "best_validation_loss":trainer.state.best_metric,"seed":20261121,"epochs":a.epochs}
    (a.output/"training.json").write_text(json.dumps(evidence,indent=2)+"\n")


if __name__=="__main__":
    main()
