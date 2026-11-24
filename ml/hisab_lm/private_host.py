"""Publish a completed text-model GGUF to the owner's private Hugging Face model repository."""
import argparse
import hashlib
import json
import math
import re
from pathlib import Path

BASES = {"Qwen/Qwen3-1.7B", "Qwen/Qwen3-4B"}
ARCHIVE = "31239d4b85ddb7fdc1aba9c21f5e41cd68f16ef687c19aed0d15bda985e7d0a9"


def sha256(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def metadata(receipt, gguf):
    training = json.loads(receipt.read_text(encoding="utf8"))
    base = training.get("base")
    revision = training.get("base_revision", "")
    loss = training.get("best_validation_loss")
    if (base not in BASES or not re.fullmatch("[a-f0-9]{40}", revision)
            or training.get("archive_sha256") != ARCHIVE or training.get("smoke")
            or training.get("epochs") not in (2, 3) or type(loss) not in (int, float)
            or not math.isfinite(loss) or loss < 0):
        raise ValueError("Expected a completed synthetic Hisab-LM training receipt with validation loss.")
    if gguf.stat().st_size < 100_000_000:
        raise ValueError("Expected a complete merged GGUF model.")
    from gguf import GGUFReader, LlamaFileType
    reader = GGUFReader(str(gguf))
    if (reader.get_field("general.file_type").contents() != int(LlamaFileType.MOSTLY_Q4_K_M)
            or reader.get_field("general.architecture").contents() != "qwen3"):
        raise ValueError("Expected Qwen3 Q4_K_M GGUF metadata.")
    if not reader.tensors or not any(t.name.startswith("blk.") for t in reader.tensors):
        raise ValueError("Missing GGUF transformer weights.")
    digest = sha256(gguf)
    return dict(base=base, base_revision=revision, epochs=training["epochs"],
                best_validation_loss=loss, archive_sha256=ARCHIVE, gguf_sha256=digest)


def upload(manifest, gguf):
    from huggingface_hub import CommitOperationAdd, HfApi, hf_hub_download
    api = HfApi(endpoint="https://huggingface.co", token=True)
    identity = api.whoami()
    if identity.get("isPro") or identity.get("canPay"):
        raise ValueError("Automatic upload requires a free personal account without paid overage.")
    repo = f"{identity['name']}/capzoryx-hisab-lm"
    api.create_repo(repo, repo_type="model", private=True, exist_ok=True)
    current = api.model_info(repo)
    if current.private is not True:
        raise ValueError("The destination model repository must be private.")
    base = manifest["base"]
    revision = manifest["base_revision"]
    files = {"hisab-lm-q4_k_m.gguf": gguf,
             "training.json": json.dumps(manifest, indent=2).encode(),
             "Modelfile": Path(__file__).with_name("Modelfile"),
             "LICENSE-CAPZORYX": Path(__file__).resolve().parents[2] / "LICENSE"}
    base_files = api.list_repo_files(base, revision=revision)
    if "LICENSE" not in base_files:
        raise ValueError("Missing base model license.")
    for name in ("LICENSE", "NOTICE", "NOTICE.txt"):
        if name in base_files:
            files[name] = hf_hub_download(base, name, revision=revision, token=False)
    files["README.md"] = (
        f"---\nlicense: apache-2.0\nbase_model: {base}\n---\n# Capzoryx Hisab-LM\n\n"
        f"Text-model candidate fine-tuned on Capzoryx synthetic financial examples. "
        f"Base revision `{revision}`. See training.json for its validation loss and digests.\n\n"
        "Private development artifact. Production task approval requires an independent financial "
        "and Bangla-language evaluation. Load locally with `ollama create hisab-lm -f Modelfile`.\n"
    ).encode()
    commit = api.create_commit(repo, repo_type="model", parent_commit=current.sha,
                              commit_message="Store trained Capzoryx Hisab-LM Q4_K_M and model provenance",
                              operations=[CommitOperationAdd(name, value) for name, value in files.items()])
    result = api.model_info(repo, revision=commit.oid, files_metadata=True)
    weight = next(f for f in result.siblings if f.rfilename == "hisab-lm-q4_k_m.gguf")
    if not result.private or not weight.lfs or weight.lfs.sha256 != manifest["gguf_sha256"]:
        raise RuntimeError("Published revision failed its privacy or checksum check.")
    print(f"https://huggingface.co/{repo}/tree/{commit.oid}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--gguf", type=Path, required=True)
    parser.add_argument("--upload", action="store_true", help="Publish to a private personal repository.")
    args = parser.parse_args()
    manifest = metadata(args.receipt, args.gguf)
    print(json.dumps(manifest, indent=2))
    if args.upload:
        provenance = json.loads(args.gguf.with_suffix(".provenance.json").read_text(encoding="utf8"))
        adapter = args.receipt.parent / "adapter" / "adapter_model.safetensors"
        if (provenance.get("gguf_sha256") != manifest["gguf_sha256"]
                or provenance.get("training_sha256") != sha256(args.receipt)
                or provenance.get("adapter_sha256") != sha256(adapter)):
            raise ValueError("The exported GGUF must match this training receipt and adapter.")
        manifest.update(training_sha256=provenance["training_sha256"], adapter_sha256=provenance["adapter_sha256"])
        upload(manifest, args.gguf)


if __name__ == "__main__":
    main()
