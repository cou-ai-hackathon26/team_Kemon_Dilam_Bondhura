"""Publish an already trained Hisab-Chat adapter without training again.

Merges the LoRA adapter into the 16-bit base model with plain Transformers and PEFT, uploads it privately, starts
the ZeroGPU Space and prints the Render values. Use it when training finished but a later step failed. In the same
Colab or Kaggle notebook (do not re-run the cell that deletes the Capzoryx folder):

    %cd /content/Capzoryx
    !git pull -q
    %run ml/hisab_chat/publish.py
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from ml.hisab_chat import kaggle_run  # noqa: E402  (secrets, repo names and the Space starter)

ADAPTER = ROOT / os.getenv('HISAB_ADAPTER', 'runs/hisab-chat/adapter')
BASE = os.getenv('HISAB_BASE', 'Qwen/Qwen3-4B-Instruct-2507')


def main():
    kaggle_run.load_secrets()
    token = os.environ.get('HF_TOKEN')
    if not token:
        raise SystemExit('Add the HF_TOKEN secret (write access) first.')
    if not (ADAPTER / 'adapter_config.json').exists():
        found = sorted(str(p.parent) for p in (ROOT / 'runs').glob('**/adapter_config.json')) if (ROOT / 'runs').exists() else []
        raise SystemExit(f'No trained adapter at {ADAPTER}. Found: {found or "none"}. Training did not finish, so it has to run '
                         'again (the new runner shows the error in the notebook).')
    import torch
    from huggingface_hub import HfApi
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer
    config = json.loads((ADAPTER / 'adapter_config.json').read_text())
    named = config.get('base_model_name_or_path') or BASE
    base = BASE if ('bnb' in named or '4bit' in named or named.startswith('unsloth/')) else named  # merge into 16-bit weights
    print(f'Merging {ADAPTER} into {base} ...', flush=True)
    try:
        model = AutoModelForCausalLM.from_pretrained(base, dtype=torch.bfloat16, low_cpu_mem_usage=True)
    except TypeError:
        model = AutoModelForCausalLM.from_pretrained(base, torch_dtype=torch.bfloat16, low_cpu_mem_usage=True)
    model = PeftModel.from_pretrained(model, str(ADAPTER)).merge_and_unload()
    tokenizer = AutoTokenizer.from_pretrained(str(ADAPTER))
    api = HfApi(token=token)
    api.create_repo(kaggle_run.MERGED_REPO, private=True, exist_ok=True)
    print(f'Uploading to https://huggingface.co/{kaggle_run.MERGED_REPO} (about 8 GB) ...', flush=True)
    model.push_to_hub(kaggle_run.MERGED_REPO, token=token, private=True)
    tokenizer.push_to_hub(kaggle_run.MERGED_REPO, token=token, private=True)
    record = ADAPTER.parent / 'training.json'
    if record.exists():
        api.upload_file(path_or_fileobj=str(record), path_in_repo='training.json', repo_id=kaggle_run.MERGED_REPO)
    if not kaggle_run.start_zerogpu_space():
        raise SystemExit('Merged weights are uploaded, but the ZeroGPU Space could not be created (see the message above).')
    space = kaggle_run.ZERO_SPACE
    print('\n=== Done ===')
    print('Space (takes ~5-10 minutes to build):', f'https://huggingface.co/spaces/{space}')
    print('\nAdd these Environment variables on Render (capzoryx-staging-api):')
    print(f"  CAPZORYX_CHAT_HISAB_URL  = https://{space.replace('/', '-').lower()}.hf.space")
    print('  CAPZORYX_CHAT_HISAB_MODE = zerogpu')
    print('  CAPZORYX_CHAT_HISAB_KEY  = a Hugging Face READ token (Settings -> Access Tokens)')


if __name__ == '__main__':
    main()
