"""Merge a completed Hisab-LM adapter on CPU and create a GGUF using a local llama.cpp release."""
import argparse
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from ml.hisab_lm.private_host import BASES, ARCHIVE


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--training", type=Path, required=True, help="Directory containing training.json and adapter/")
    p.add_argument("--llama-cpp", type=Path, required=True, help="Source tree with convert_hf_to_gguf.py")
    p.add_argument("--quantizer", type=Path, required=True, help="llama-quantize executable from the same release")
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    receipt = json.loads((args.training / "training.json").read_text(encoding="utf8"))
    if receipt["base"] not in BASES or receipt["archive_sha256"] != ARCHIVE or receipt["epochs"] not in (2, 3):
        raise ValueError("Expected the completed Qwen synthetic training run.")
    if args.out.exists():
        raise FileExistsError(args.out)
    import torch
    from huggingface_hub import snapshot_download
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer
    base_dir = snapshot_download(receipt["base"], revision=receipt["base_revision"], token=False, local_files_only=True)
    base = AutoModelForCausalLM.from_pretrained(base_dir, dtype=torch.bfloat16, device_map={"": "cpu"})
    adapter = PeftModel.from_pretrained(base, args.training / "adapter")
    merged = adapter.merge_and_unload(safe_merge=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=args.out.parent) as private:
        dest = Path(private)
        merged.save_pretrained(dest / "merged", safe_serialization=True)
        AutoTokenizer.from_pretrained(base_dir).save_pretrained(dest / "merged")
        del merged, adapter, base
        subprocess.run([sys.executable, str(args.llama_cpp / "convert_hf_to_gguf.py"), str(dest / "merged"),
                        "--outtype", "f16", "--outfile", str(dest / "merged.gguf")], check=True)
        subprocess.run([str(args.quantizer.resolve()), str(dest / "merged.gguf"),
                        str(dest / "candidate.gguf"), "Q4_K_M"], check=True)
        from ml.hisab_lm.private_host import metadata, sha256
        report = metadata(args.training / "training.json", dest / "candidate.gguf")
        report["training_sha256"] = sha256(args.training / "training.json")
        report["adapter_sha256"] = sha256(args.training / "adapter" / "adapter_model.safetensors")
        (dest / "candidate.gguf").rename(args.out)
        args.out.with_suffix(".provenance.json").write_text(json.dumps(report, indent=2), encoding="utf8")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
