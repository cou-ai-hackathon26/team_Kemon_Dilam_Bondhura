"""Evaluate recorded clips through the speech adapter and the app's deterministic parser. Outputs counts only."""
import argparse
import base64
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess
import time
from urllib.parse import urlsplit
import wave

from api.app import gateway
from api.app.agents.errors import ProviderError
from api.app.voice import speech_provider, transcribe, waveform
from evals.run import leakage_check, normalize, predictions, read_rows, score

ROOT=Path(__file__).resolve().parents[2]


def entries(directory):
    manifest=directory/'manifest.jsonl'
    if manifest.is_file():
        return [json.loads(line) for line in manifest.read_text().splitlines() if line.strip()]
    return [json.loads(p.read_text()) for p in sorted(directory.glob('*.json'))]


def load_audio(path):
    if path.stat().st_size>2*1024*1024:
        raise ValueError('Clip exceeds the field kit size limit')
    # Decode local field-kit media into stripped mono PCM. Limit CPU time, streams and decoded duration.
    pcm=subprocess.run(['ffmpeg','-nostdin','-loglevel','error','-protocol_whitelist','file,pipe',
                        '-f',{'webm':'matroska','m4a':'mov','wav':'wav','ogg':'ogg'}[path.suffix[1:]],
                        '-i','pipe:0','-map','0:a:0','-ac','1','-ar','16000','-t','30.01','-f','s16le','pipe:1'],
                       input=path.read_bytes(),capture_output=True,timeout=9,check=True).stdout
    buffer=io.BytesIO()
    with wave.open(buffer,'wb') as file:
        file.setnchannels(1);file.setsampwidth(2);file.setframerate(16000);file.writeframes(pcm)
    return waveform(base64.b64encode(buffer.getvalue()))


def prepare(directory,rows,provider,training):
    if not rows or len(rows)>1000:raise ValueError('Provide 1–1000 labelled clips')
    ids=set();hashes=set();texts=set()
    human=all(row.get('source')=='team' for row in rows)
    if not human and not all(row.get('source')=='machine_voice' for row in rows):
        raise ValueError('Use separate corpora for machine and independent recordings')
    if human:
        if not training:raise ValueError('Supply the training manifest to screen independent examples for overlap')
        leakage_check(rows,training)
        training_rows,_=read_rows(training)
        training_speakers={r['contributor_ref'] for r in training_rows if r.get('contributor_ref')}
        if any(r.get('contributor_ref') in training_speakers for r in rows):
            raise ValueError('Training and independent evaluation speakers must be disjoint')
    for row in rows:
        identity=row.get('id','')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',identity) or identity in ids:raise ValueError('Invalid or duplicate clip ID')
        path=(directory/row['audio_file']).resolve()
        if path.parent!=directory or path.suffix not in {'.wav','.webm','.m4a','.ogg'}:raise ValueError('Unexpected clip path')
        if row.get('language') not in {'en','bn','bn-Latn'} or type(row.get('amount_minor'))!=int or not 0<row['amount_minor']<=10**12 or row.get('direction') not in {'inflow','outflow','due'}:
            raise ValueError('Bad clip labels')
        if human:
            if not row.get('contributor_ref') or row.get('consent_scope')!='speech_evaluation_only':
                raise ValueError('Human clips require pseudonymous contributor reference and consent')
            if provider[0]!='hisab_asr' and row.get('consented_provider_host')!=urlsplit(provider[1]).hostname:
                raise ValueError('External speech evaluation requires separate consent for this provider')
        expected=normalize(row['text'])
        if expected in texts:raise ValueError('Repeated reference sentence')
        sig=hashlib.sha256(path.read_bytes()).hexdigest()
        if sig in hashes or row.get('audio_sha256',sig)!=sig:raise ValueError('Duplicate audio or checksum mismatch')
        ids.add(identity);hashes.add(sig);texts.add(expected)
    return human,hashlib.sha256(''.join(sorted(hashes)).encode()).hexdigest()


def main():
    cli=argparse.ArgumentParser(description=__doc__);cli.add_argument('directory',type=Path)
    cli.add_argument('--training-manifest',type=Path)
    args=cli.parse_args();directory=args.directory.resolve();rows=entries(directory)
    try:provider=speech_provider()
    except ProviderError as error:cli.exit(2,'Speech evaluation unavailable: '+error.code+'\n')
    human,checksum=prepare(directory,rows,provider,args.training_manifest)
    outputs=[];elapsed=[]
    os.environ['SAFELEDGER_SIGNING_KEY']=secrets.token_hex(32)
    with sqlite3.connect(':memory:') as db:
        gateway.schema(db)
        for row in rows:
            audio=load_audio(directory/row['audio_file'])
            start=time.monotonic()
            try:
                text=transcribe(audio,'en' if row['language']=='en' else 'bn',db,'local-voice-eval',provider,evaluation=True)
            except ProviderError as error:
                if error.code in {'AI_QUOTA_EXHAUSTED','AI_CIRCUIT_OPEN','AI_DAILY_BUDGET_EXCEEDED'}:
                    cli.exit(2,'Incomplete speech evaluation: '+error.code+'\n')
                text=''
            # Include the actual client parser in the measured path. Fresh Node startup makes timing conservative.
            payload=[dict(id=row['id'],text=text,as_of=row['as_of'])]
            prediction=subprocess.run(['node',str(ROOT/'evals/extract.cjs')],input=json.dumps(payload),capture_output=True,text=True,check=True,timeout=15)
            outputs.extend(json.loads(prediction.stdout));elapsed.append(time.monotonic()-start)
    totals=score(rows,predictions(outputs,{r['id'] for r in rows}))
    def rate(field):return totals[field]['correct']/max(1,totals[field]['total'])
    report=dict(suite='voice',source='independent_speakers' if human else 'machine_voice',provider_base=provider[1],
                model=provider[2],clips=len(rows),distinct_speakers=len({r['contributor_ref'] for r in rows}) if human else 0,
                amount_accuracy=rate('amount_minor'),kind_accuracy=rate('direction'),party_accuracy=rate('party'),
                p95_seconds=sorted(elapsed)[math.ceil(len(elapsed)*.95)-1],corpus_sha256=checksum)
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
