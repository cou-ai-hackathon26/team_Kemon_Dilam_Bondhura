"""Create 30 labelled machine-voiced finance clips with espeakng-loader 0.2.4 and FFmpeg (QA tools only)."""
import argparse
import ctypes
import hashlib
import io
import json
from pathlib import Path
import subprocess
import wave
import zipfile


def speak_library():
    import espeakng_loader as loader
    lib=loader.load_library()
    if not lib:
        raise RuntimeError('Install espeakng-loader==0.2.4 in the QA Python environment')
    rate=lib.espeak_Initialize(2,0,loader.get_data_path().encode(),0)
    if rate<=0:raise RuntimeError('Could not initialize eSpeak NG')
    samples=bytearray()
    callback=ctypes.CFUNCTYPE(ctypes.c_int,ctypes.c_void_p,ctypes.c_int,ctypes.c_void_p)
    @callback
    def collect(data,count,_events):
        if data and count>0:samples.extend(ctypes.string_at(data,count*2))
        return 0
    lib._capture_callback=collect  # ctypes callbacks must remain alive across synthesis calls.
    lib.espeak_SetSynthCallback.argtypes=[callback]
    lib.espeak_SetSynthCallback(collect)
    lib.espeak_Synth.argtypes=[ctypes.c_char_p,ctypes.c_size_t,ctypes.c_uint,ctypes.c_int,ctypes.c_uint,
                               ctypes.c_uint,ctypes.c_void_p,ctypes.c_void_p]
    def speak(text,voice,speed):
        samples.clear()
        if lib.espeak_SetVoiceByName(voice.encode())!=0:raise RuntimeError('Missing voice '+voice)
        lib.espeak_SetParameter(1,speed,0)
        data=text.encode()
        result=lib.espeak_Synth(data,len(data)+1,0,1,0,1,None,None)
        if result or lib.espeak_Synchronize()!=0 or not samples:raise RuntimeError('Failed speech synthesis')
        pcm=subprocess.run(['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-f','s16le','-ar',str(rate),
                            '-ac','1','-i','pipe:0','-f','s16le','-ar','16000','-ac','1','pipe:1'],
                           input=bytes(samples),capture_output=True,check=True).stdout
        out=io.BytesIO()
        with wave.open(out,'wb') as f:
            f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000);f.writeframes(pcm)
        return out.getvalue()
    return speak


def rows():
    names=['Karim','Asha','Mina','Hasan','Anika','Babul','Raju','Farida','Nasir','Rina']
    native=['করিম','আশা','মিনা','হাসান','অনিকা','বাবুল','রাজু','ফরিদা','নাসির','রিনা']
    for language in ['en','bn','bn-Latn']:
        for i,(name,bangla_name) in enumerate(zip(names,native)):
            amount=120*(i+1)
            bn_amount=str(amount).translate(str.maketrans('0123456789','০১২৩৪৫৬৭৮৯'))
            direction=['inflow','outflow','due'][i%3]
            if language=='en':
                sentence=[f'Received {amount} taka from {name} today',f'Paid {amount} taka to {name} today',
                          f'Bill from {name} {amount} taka due tomorrow'][i%3]
            elif language=='bn':
                sentence=[f'{bangla_name} থেকে {bn_amount} টাকা পেলাম',f'{bangla_name} কে {bn_amount} টাকা দিলাম',
                          f'{bangla_name} এর বিল {bn_amount} টাকা বাকি'][i%3]
            else:
                sentence=[f'{name} theke {amount} taka pelam',f'{name} ke {amount} taka dilam',
                          f'{name} er bill {amount} taka baki'][i%3]
            yield dict(id=f'{language}-{i+1:02}',text=sentence,language=language,
                       amount_minor=amount*100,direction=direction,party=bangla_name if language=='bn' else name,
                       source='machine_voice',as_of='2026-11-21')


def main():
    cli=argparse.ArgumentParser(description=__doc__);cli.add_argument('output',type=Path);args=cli.parse_args()
    speak=speak_library();manifest=[]
    with zipfile.ZipFile(args.output,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        def write(name,data):
            member=zipfile.ZipInfo(name,date_time=(2026,11,21,0,0,0));member.compress_type=zipfile.ZIP_DEFLATED
            archive.writestr(member,data)
        for i,row in enumerate(rows()):
            voice='bn' if row['language']=='bn' else 'en'
            audio=speak(row['text'],voice,145+i%3*15)
            file=row['id']+'.wav';write(file,audio)
            manifest.append(dict(row,audio_file=file,audio_sha256=hashlib.sha256(audio).hexdigest(),
                                 synthesizer='eSpeak NG',voice=voice))
        write('manifest.jsonl',''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in manifest))
        write('README.txt','Capzoryx voice smoke corpus\n30 synthetic machine-voiced statements in English, Bangla and transliterated Bangla. eSpeak NG uses its English voice for the Latin-script samples. These clips exercise recording and parser integration. Independent human-speaker validation requires a separate consented corpus.\n')
    print(f'{len(manifest)} synthetic clips written to {args.output}')


if __name__=='__main__':main()
