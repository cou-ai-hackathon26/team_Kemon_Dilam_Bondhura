import os
import signal
import socket
import subprocess
import time

def stop_process(process):
    if process is None:return
    if os.name=='posix':
        try:os.killpg(process.pid,signal.SIGTERM)
        except ProcessLookupError:pass
    else:process.terminate()
    try:process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        if os.name=='posix':
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
        else:process.kill()
        process.wait(timeout=5)


def verify_ports_closed(ports):
    for _ in range(50):
        alive=[]
        for number in ports:
            with socket.socket() as sock:
                sock.settimeout(.1)
                if sock.connect_ex(('127.0.0.1',number))==0:alive.append(number)
        if not alive:return
        time.sleep(.05)
    raise RuntimeError('Harness left a listening child process behind.')
