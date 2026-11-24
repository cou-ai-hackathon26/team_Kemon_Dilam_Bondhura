"""Prepare the optional, same-origin browser speech pack. No application or microphone data."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

WEB = Path(__file__).resolve().parents[1]
RELEASES = 'https://github.com/k2-fsa/sherpa-onnx/releases/download'
RUNTIME = 'sherpa-onnx-wasm-simd-1.12.23-vad-asr-en-whisper_tiny'
MODELS = 'sherpa-onnx-whisper-tiny'
ENGLISH = [
    ('v1.12.23', RUNTIME, '40f2dffc5b44bd0ca74482dafcda87180ea808aabde7c84accf584bcd7018e1b',
     ['sherpa-onnx-asr.js', 'sherpa-onnx-wasm-main-vad-asr.js', 'sherpa-onnx-wasm-main-vad-asr.wasm']),
    ('asr-models', MODELS, 'c46116994e539aa165266d96b325252728429c12535eb9d8b6a2b10f129e66b1',
     ['tiny-encoder.int8.onnx', 'tiny-decoder.int8.onnx', 'tiny-tokens.txt']),
]
ONLINE = 'sherpa-onnx-wasm-simd-v1.12.23-en-asr-zipformer'
BENGALI = 'sherpa-onnx-streaming-zipformer-bn-vosk-2026-02-09'
PACKS = {'whisper-tiny-v1': ENGLISH, 'vosk-bn-v1': [
    ('v1.12.23', ONLINE, '216f7329c99a515a32bf941e9b70b269bacfd1fb1888b7dcd321311b1735215c',
     ['sherpa-onnx-asr.js', 'sherpa-onnx-wasm-main-asr.js', 'sherpa-onnx-wasm-main-asr.wasm']),
    ('asr-models', BENGALI, '1bc89d81ee33ba7ddef8454736aeeb55e3cc1593274272485c6da342202b4b18',
     ['encoder.onnx', 'decoder.onnx', 'joiner.onnx', 'tokens.txt']),
]}


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest() if hasattr(hashlib, 'file_digest') else _digest(stream)


def _digest(stream):
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
        digest.update(chunk)
    return digest.hexdigest()


def prepare(pack, destination, cache):
    destination.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    assets = {}
    for tag, name, digest, files in PACKS[pack]:
        archive = cache / (name + '.tar.bz2')
        if not archive.exists() or sha256(archive) != digest:
            with tempfile.NamedTemporaryFile(dir=cache) as download:
                subprocess.run(['curl', '--fail', '--silent', '--show-error', '--location', '--retry', '2',
                                '--max-time', '240', '--output', download.name,
                                f'{RELEASES}/{tag}/{archive.name}'], check=True)
                if sha256(Path(download.name)) != digest:
                    raise ValueError('Speech archive checksum mismatch: ' + name)
                shutil.copyfile(download.name, archive)
        with tarfile.open(archive, 'r|bz2') as contents:
            selected = {name + '/' + file: file for file in files}
            for entry in contents:
                key = entry.name.removeprefix('./')
                if key not in selected:
                    continue
                file = selected.pop(key)
                if not entry.isfile() or not 0 < entry.size < 100_000_000:
                    raise ValueError('Unexpected speech asset: ' + file)
                target = destination / file
                with contents.extractfile(entry) as source, target.open('wb') as output:
                    shutil.copyfileobj(source, output)
                assets[file] = {'bytes': entry.size, 'sha256': sha256(target)}
            if selected:
                raise ValueError('Missing speech assets: ' + str(selected))
    (destination / 'manifest.json').write_text(json.dumps({'version': pack, 'licenses': '/speech-licenses/NOTICE.txt', 'files': assets}) + '\n')
    print(f'Speech pack {pack}: {sum(f["bytes"] for f in assets.values()):,} bytes in {len(assets)} optional files')


if __name__ == '__main__':
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else WEB / 'dist'
    cache = Path(os.environ.get('CAPZORYX_VOICE_BUILD_CACHE', WEB / 'node_modules/.cache/capzoryx-speech'))
    for pack in PACKS:
        prepare(pack, output / 'voice-pack' / pack, cache)
