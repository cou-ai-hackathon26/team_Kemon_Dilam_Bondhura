"""One-cell Kaggle run: build data, train Hisab-Chat, publish it and start its free Hugging Face Space.

In a Kaggle notebook (Accelerator: GPU T4 x2, Internet: on) add these secrets under Add-ons -> Secrets:
HF_TOKEN (Hugging Face write token) and any of GROQ_KEY, GEMINI_KEY, CEREBRAS_KEY, OPENROUTER_KEY. Then run:

    !rm -rf Capzoryx && git clone -q https://github.com/Pranto-404/Capzoryx
    %run Capzoryx/ml/hisab_chat/kaggle_run.py

Use Save Version -> Save & Run All to keep it running with the browser closed.

Google Colab (Pro A100 is fastest): Runtime -> Change runtime type -> A100. Add the same names under Secrets (the key
icon on the left, turn on notebook access) and run the same two lines. With 20 GB or more of GPU memory (L4, A100) the run uses
16-bit LoRA and all the data; an A100 also gets a second epoch. Keep the tab open until it finishes. It ends by printing the values
to add on Render.

ZeroGPU (needs a Hugging Face PRO account): put `%env HISAB_TARGET=zerogpu` above the %run line. The run then also
uploads merged 16-bit weights and starts a private Gradio Space on ZeroGPU, which answers far faster than the free
CPU Space. Total time on a T4 is about 2 hours (data ~25 min with teachers, training ~60-75 min, upload ~15 min).
"""
import os
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def gpu_memory_gb():
    try:
        out = subprocess.run(['nvidia-smi', '--query-gpu=memory.total', '--format=csv,noheader,nounits'],
                             capture_output=True, text=True, check=True).stdout.split()
        return int(out[0]) / 1024 if out else 0
    except (OSError, subprocess.CalledProcessError, ValueError):
        return 0


OWNER = os.getenv('HISAB_OWNER', 'Pranto-404')
MODEL_REPO, SPACE_REPO = f'{OWNER}/hisab-chat-gguf', f'{OWNER}/hisab-chat'
MERGED_REPO, ZERO_SPACE = f'{OWNER}/hisab-chat-merged', f'{OWNER}/hisab-chat-zero'
ZEROGPU = os.getenv('HISAB_TARGET', '').lower() == 'zerogpu'
# Default Qwen3-4B-Instruct-2507 (good Bangla, served on ZeroGPU). HISAB_BASE=Qwen/Qwen3-1.7B is faster and also gets
# a free CPU copy.
BASE = os.getenv('HISAB_BASE', 'Qwen/Qwen3-4B-Instruct-2507')
SMALL = '1.7B' in BASE
# Data size: 1.0 is about 6,900 conversations. The 4B default uses 60% so the whole run fits in about 2 hours on a T4.
MEMORY = gpu_memory_gb()
BIG_GPU = MEMORY >= 20  # Colab L4 (24 GB) or A100: 16-bit LoRA instead of QLoRA, and all the data
SCALE = float(os.getenv('HISAB_SCALE', '1.0' if SMALL or BIG_GPU else '0.6'))
EPOCHS = os.getenv('HISAB_EPOCHS', '2' if MEMORY >= 35 else '1')  # A100: a second epoch fits in the time
# A free 2-core CPU Space is only fast enough for the small model; the 4B one is served on ZeroGPU alone.
CPU_COPY = SMALL or not ZEROGPU or os.getenv('HISAB_CPU') == '1'
TEACHER_SECRETS = {'GROQ_KEY': 'CAPZORYX_CHAT_GROQ_KEY', 'GEMINI_KEY': 'CAPZORYX_CHAT_GEMINI_KEY',
                   'CEREBRAS_KEY': 'CAPZORYX_CHAT_CEREBRAS_KEY', 'OPENROUTER_KEY': 'CAPZORYX_CHAT_OPENROUTER_KEY',
                   'GITHUB_KEY': 'CAPZORYX_CHAT_GITHUB_KEY'}  # GitHub Models: a GitHub token with models:read (Student Pro works)


def step(title, *command):
    """Run a step and pass its output through, so progress bars show in Colab too (it hides child output otherwise)."""
    print(f'\n=== {title} ===', flush=True)
    proc = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for chunk in iter(lambda: proc.stdout.read1(4096), b''):
        sys.stdout.write(chunk.decode('utf-8', errors='replace'))
        sys.stdout.flush()
    if proc.wait():
        raise subprocess.CalledProcessError(proc.returncode, command)


def load_secrets():
    """Kaggle secrets, Colab secrets (the key icon on the left, with notebook access on), or the environment."""
    try:
        from kaggle_secrets import UserSecretsClient
        get = UserSecretsClient().get_secret
    except ImportError:
        try:
            from google.colab import userdata
            get = userdata.get
        except ImportError:  # elsewhere the values come from the environment
            return
    for name, env in {'HF_TOKEN': 'HF_TOKEN', **TEACHER_SECRETS}.items():
        try:
            value = get(name)
            if value:
                os.environ[env] = value
        except Exception:  # noqa: BLE001 - a missing optional secret is fine
            pass



def probe_teachers():
    """Pick each teacher's current model and send it one tiny request, so problems show at the start."""
    import httpx
    sys.path.insert(0, str(ROOT))
    from api.app.chat import llm
    working = []
    for provider in [p for p in llm.available() if not p.own]:
        provider.discover()
        try:
            reply = httpx.post(provider.url + '/chat/completions', timeout=30,
                               headers={'Authorization': 'Bearer ' + provider.key},
                               json={'model': provider.model_name, 'max_tokens': 5, 'messages': [{'role': 'user', 'content': 'hi'}]})
            status = reply.status_code
            detail = '' if status == 200 else ' ' + reply.text[:160].replace('\n', ' ')
        except httpx.HTTPError as error:
            status, detail = 'error', ' ' + type(error).__name__
        print(f'Teacher {provider.name}: model {provider.model_name} -> {status}{detail}', flush=True)
        if status == 200:
            working.append(provider.name)
            os.environ[f'CAPZORYX_CHAT_{provider.name.upper()}_MODEL'] = provider.model_name  # reuse in child steps
    print('Working teachers:', ', '.join(working) or 'none (template data only)', flush=True)
    return working


def start_space():
    from huggingface_hub import HfApi
    api = HfApi()
    api.create_repo(SPACE_REPO, repo_type='space', space_sdk='docker', exist_ok=True)
    for name in ('Dockerfile', 'README.md'):
        api.upload_file(path_or_fileobj=str(ROOT / 'ml/hisab_chat/space' / name), path_in_repo=name, repo_id=SPACE_REPO,
                        repo_type='space', commit_message='Serve Hisab-Chat')
    key = secrets.token_urlsafe(32)
    api.add_space_secret(SPACE_REPO, 'HF_TOKEN', os.environ['HF_TOKEN'])
    api.add_space_secret(SPACE_REPO, 'LLAMA_API_KEY', key)
    return key


def start_zerogpu_space():
    """A private Gradio Space on ZeroGPU that serves the merged model. Hosting ZeroGPU needs a PRO account."""
    from huggingface_hub import HfApi
    api = HfApi()
    try:
        api.create_repo(ZERO_SPACE, repo_type='space', space_sdk='gradio', space_hardware='zero-a10g', private=True, exist_ok=True)
    except Exception as error:  # noqa: BLE001 - explain the usual cause instead of a stack trace
        print('Could not create the ZeroGPU Space (ZeroGPU hosting needs Hugging Face PRO):', error)
        return False
    for name in ('app.py', 'requirements.txt', 'README.md'):
        api.upload_file(path_or_fileobj=str(ROOT / 'ml/hisab_chat/zerogpu' / name), path_in_repo=name, repo_id=ZERO_SPACE,
                        repo_type='space', commit_message='Serve Hisab-Chat on ZeroGPU')
    api.add_space_secret(ZERO_SPACE, 'HF_TOKEN', os.environ['HF_TOKEN'])
    api.add_space_variable(ZERO_SPACE, 'MODEL_REPO', MERGED_REPO)
    api.add_space_variable(ZERO_SPACE, 'GPU_SECONDS', '12' if SMALL else '20')
    return True


def main():
    load_secrets()
    print(f'GPU memory: {MEMORY:.0f} GB; base {BASE}; data x{SCALE}; epochs {EPOCHS}', flush=True)
    if not os.getenv('HF_TOKEN'):
        raise SystemExit('Add a Hugging Face write token as the secret HF_TOKEN (Kaggle: Add-ons -> Secrets; Colab: the key icon, with notebook access on).')
    teachers = probe_teachers()
    step('Install training libraries', sys.executable, '-m', 'pip', '-q', 'install', 'unsloth', 'httpx')
    # Short everyday chat plus whole-business answers (any industry), so the model knows the owner's business.
    n = lambda count: str(int(count * SCALE))
    # The base model answers open business questions in the production format, so the tuned model keeps its general
    # ability; templates alone taught the first model to repeat stock replies to anything new.
    step('Write open answers with the base model', sys.executable, '-m', 'ml.hisab_chat.self_teach', '--out', 'data/selfteach.jsonl',
         '--count', n(int(os.getenv('HISAB_SELFTEACH', '600'))), '--base', BASE)
    step('Build conversations', sys.executable, '-m', 'ml.hisab_chat.build_dataset', '--out', 'data/hisab_chat',
         '--size', n(1200), '--teacher', n(400) if teachers else '0',
         '--business', n(1000), '--business-teacher', n(400) if teachers else '0', '--extra', 'data/selfteach.jsonl')
    step('Train, export and upload', sys.executable, '-m', 'ml.hisab_chat.train', '--data', 'data/hisab_chat',
         '--output', 'runs/hisab-chat', '--epochs', EPOCHS, '--base', BASE, *([] if SMALL or BIG_GPU else ['--qlora']),
         *(['--gguf', '--push', MODEL_REPO] if CPU_COPY else []))
    if ZEROGPU:
        # Merge and upload with plain Transformers + PEFT (publish.py, which can be re-run alone if this step fails),
        # start the ZeroGPU Space and print its Render values.
        from ml.hisab_chat import publish
        publish.main()
        if not CPU_COPY:
            return
    key = start_space() if CPU_COPY else None  # the free CPU copy answers when GPU quota is used up (small model only)
    if ZEROGPU:
        print('\nAlso add the free CPU copy (used when the GPU quota runs out):')
        print(f"  CAPZORYX_CHAT_HISAB_CPU_URL  = https://{SPACE_REPO.replace('/', '-').lower()}.hf.space/v1")
        print(f'  CAPZORYX_CHAT_HISAB_CPU_KEY  = {key}')
        return
    if not key:
        raise SystemExit('The ZeroGPU Space could not be created and no CPU copy was built. Rerun with HISAB_BASE=Qwen/Qwen3-1.7B.')
    print('\n=== Done ===')
    print('Model:', f'https://huggingface.co/{MODEL_REPO}')
    print('Space (takes ~5 minutes to start):', f'https://huggingface.co/spaces/{SPACE_REPO}')
    print('\nAdd these two Environment variables on Render (capzoryx-staging-api):')
    print(f"  CAPZORYX_CHAT_HISAB_URL = https://{SPACE_REPO.replace('/', '-').lower()}.hf.space/v1")
    print(f'  CAPZORYX_CHAT_HISAB_KEY = {key}')


if __name__ == '__main__':
    main()
