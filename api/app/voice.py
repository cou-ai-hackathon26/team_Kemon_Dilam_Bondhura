"""Bounded voice capture. Only PCM waveforms leave this process, never file metadata."""
import base64
import binascii
import json
import os
import re
import struct
import time
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends
from pydantic import Field, StrictBool

from api.app import gateway, security as sec
from api.app.agents.errors import ProviderError
from api.app.assist import smart_enabled
from api.app.contracts import Contract
from api.app.routes import principal, db_connection

router=APIRouter(prefix='/api/assist')
TRANSPORT=None
SAMPLE_RATE=16000
MAX_FRAMES=30*SAMPLE_RATE


def waveform(encoded):
    try:
        raw=base64.b64decode(encoded,validate=True)
    except (ValueError,binascii.Error):
        sec.fail(422,'INVALID_VOICE_AUDIO')
    if len(raw)<46 or len(raw)>44+MAX_FRAMES*2 or len(raw)%2:
        sec.fail(422,'INVALID_VOICE_DURATION')
    # Accept the minimal 44-byte PCM header emitted by the recorder; discard all other chunks/metadata.
    fields=struct.unpack('<4sI4s4sIHHIIHH4sI',raw[:44])
    if fields!=(b'RIFF',len(raw)-8,b'WAVE',b'fmt ',16,1,1,SAMPLE_RATE,SAMPLE_RATE*2,2,16,b'data',len(raw)-44):
        sec.fail(422,'INVALID_VOICE_AUDIO')
    return raw


def speech_provider():
    """Zero-retention speech adapter for OpenAI-compatible /v1/audio/transcriptions."""
    model=os.getenv('SAFELEDGER_ASR_MODEL','')
    if not re.fullmatch(r'[A-Za-z0-9._:/-]{1,120}',model):
        raise ProviderError('VOICE_NOT_CONFIGURED')
    local=os.getenv('SAFELEDGER_LOCAL_ASR_URL','').rstrip('/')
    if local:
        try:
            parts=urlsplit(local)
            if parts.scheme not in {'http','https'} or parts.hostname not in {'127.0.0.1','::1'} or not parts.port or parts.username or parts.password or parts.query or parts.fragment or parts.path!='/v1':
                raise ValueError()
        except ValueError:
            raise ProviderError('AI_CONFIGURATION_INVALID') from None
        return 'hisab_asr',local,model,{}
    if os.getenv('SAFELEDGER_ASR_ZERO_RETENTION_CONFIRMED')!='1':
        raise ProviderError('VOICE_RETENTION_UNCONFIRMED')
    from api.app.agents.provider import ChatProvider
    provider=ChatProvider(model=model)
    gateway.permitted(provider,'record_data',True)
    return provider.profile,provider.base,provider.model,provider.headers()


def quality_ok(base,model):
    try:
        report=json.loads(os.getenv('SAFELEDGER_ASR_EVAL_JSON','{}'))
        return (report['suite']=='voice' and report['source']=='independent_speakers'
                and report['provider_base']==base and report['model']==model
                and type(report['clips']) is int and report['clips']>=30
                and type(report['distinct_speakers']) is int and 2<=report['distinct_speakers']<=report['clips']
                and all(type(report[key]) in (float,int) and minimum<=report[key]<=1 for key,minimum in
                        [('amount_accuracy',0.9),('kind_accuracy',0.9),('party_accuracy',0.8)])
                and type(report['p95_seconds']) in (float,int) and 0<report['p95_seconds']<6)
    except (ValueError,TypeError,KeyError):
        return False


def available_provider():
    try:
        _,base,model,_=speech_provider()
        if quality_ok(base,model):
            return {'host':urlsplit(base).hostname,'model':model}
    except ProviderError:
        pass
    return None


def transcribe(audio,lang,db,business,provider,*,evaluation=False):
    profile,base,model,headers=provider
    if not evaluation and not quality_ok(base,model):
        raise ProviderError('VOICE_QUALITY_GATE_PENDING')
    def send():
        # Audio goes directly from bounded request memory to the provider. No cache, temp files or payload logs.
        started=time.monotonic()
        try:
            with httpx.Client(transport=TRANSPORT,follow_redirects=False,trust_env=False,timeout=6) as client:
                with client.stream('POST',base+'/audio/transcriptions',headers=headers,
                                   data={'model':model,'language':lang,'response_format':'json'},
                                   files={'file':('voice.wav',audio,'audio/wav')}) as response:
                    if response.status_code==429:raise ProviderError('AI_QUOTA_EXHAUSTED')
                    if response.status_code!=200:raise ProviderError('AI_PROVIDER_REJECTED',http_status=response.status_code)
                    result=bytearray()
                    for chunk in response.iter_bytes():
                        if len(result)+len(chunk)>16384:raise ProviderError('AI_OUTPUT_BUDGET_EXCEEDED')
                        if time.monotonic()-started>6:raise ProviderError('AI_TIMEOUT')
                        result.extend(chunk)
            transcript=json.loads(result)['text'].strip()
            if not isinstance(transcript,str) or not 1<=len(transcript)<=600:
                raise ProviderError('INVALID_VOICE_TRANSCRIPT')
            return transcript,None
        except httpx.HTTPError:
            raise ProviderError('AI_TIMEOUT_OR_NETWORK_FAILURE') from None
        except (ValueError,KeyError,TypeError,AttributeError):
            raise ProviderError('INVALID_VOICE_TRANSCRIPT') from None
    trace_context=getattr(gateway,'request_trace',None)
    trace=(trace_context.get() if trace_context is not None else None) or uuid4().hex
    return gateway.metered(db,'voice',business,trace,profile,model,'record_data',
                           (len(audio)-44)//160+1,300,0,send)


class AudioBody(Contract):
    audio_b64:str=Field(min_length=1,max_length=1280060)
    language:str=Field(pattern=r'^(en|bn)$')
    consent:StrictBool
    expected_host:str=Field(min_length=1,max_length=253)
    expected_model:str=Field(min_length=1,max_length=120)


@router.post('/voice')
def capture(body:AudioBody,actor=Depends(principal),db=Depends(db_connection)):
    if actor.role!='owner':sec.fail(403,'OWNER_REQUIRED')
    if not body.consent or not smart_enabled(db,actor):sec.fail(403,'VOICE_CONSENT_REQUIRED')
    sec.budgets(db,[('voice:'+actor.user_id,8),('assist-all',40)])
    provider=speech_provider()
    _,base,model,_=provider
    if (urlsplit(base).hostname,model)!=(body.expected_host,body.expected_model):
        sec.fail(409,'VOICE_PROVIDER_CHANGED')
    audio=waveform(body.audio_b64)
    return dict(transcript=transcribe(audio,body.language,db,actor.business_id,provider),simulation_only=True)
