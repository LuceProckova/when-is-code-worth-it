"""Shared helpers for the thesis runner, the calibration and the release: paths, hashes and versions.

Nothing here writes outside the folder it is given, and nothing here calls a calendar.
"""
import glob
import hashlib
import json
import os
import platform
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
THESIS = os.path.dirname(HERE)
LIVE_APP = r'C:\Users\lucyp\Desktop\All-My-Stuff\0-AI-Solutions\08-Master-Interface\02 - App'
REQUESTS = os.path.join(THESIS, '10-Test-Data', 'requests.csv')
OLLAMA = 'http://127.0.0.1:11434'
MODEL = 'qwen2.5:7b'


def data_dir(app):
    return os.path.join(os.path.dirname(app), '03 - My Data')


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 16), b''):
            h.update(chunk)
    return h.hexdigest()


def sha_text(s):
    return hashlib.sha256(s.encode('utf-8')).hexdigest()


def flow_path(data, fid):
    return os.path.join(data, 'workflows', fid + '.json')


def flow_tables(data, fid):
    """Every csv in each knowledge folder the flow's steps name in 'uses' (e.g. thesis/naming -> knowledge/thesis)."""
    with open(flow_path(data, fid), encoding='utf-8') as f:
        wf = json.load(f)
    folders = sorted({u.split('/')[0] for s in wf['steps'] for u in (s.get('uses') or [])})
    out = []
    for fo in folders:
        out += sorted(glob.glob(os.path.join(data, 'knowledge', fo, '*.csv')))
    return out


def code_files(app):
    """The code that turns a request into events: the engine, the connector layer and every steps module."""
    files = [os.path.join(app, n) for n in ('papertrail.py', 'connections.py') if os.path.exists(os.path.join(app, n))]
    return files + sorted(glob.glob(os.path.join(app, 'steps', '*.py')))


def prompt_templates(data, fid):
    with open(flow_path(data, fid), encoding='utf-8') as f:
        wf = json.load(f)
    return {s['id']: sha_text(s['prompt']) for s in wf['steps'] if s.get('prompt')}


def file_hashes(app, flows, requests=REQUESTS, extra=()):
    """{label: sha256}. Labels are stable relative names so two runs can be compared line by line."""
    data = data_dir(app)
    h = {'requests.csv': sha256(requests)}
    for fid in flows:
        h['flow/' + fid + '.json'] = sha256(flow_path(data, fid))
        for t in flow_tables(data, fid):
            h['tables/' + os.path.relpath(t, os.path.join(data, 'knowledge')).replace('\\', '/')] = sha256(t)
        for sid, ph in prompt_templates(data, fid).items():
            h['prompt/%s/%s' % (fid, sid)] = ph
    for c in code_files(app):
        h['code/' + os.path.relpath(c, app).replace('\\', '/')] = sha256(c)
    for e in extra:
        h['runner/' + os.path.basename(e)] = sha256(e)
    return h


def combined(hashes, prefix):
    """One hash over the sorted per-file hashes with this prefix, e.g. all tables."""
    return sha_text('\n'.join('%s %s' % (k, v) for k, v in sorted(hashes.items()) if k.startswith(prefix)))


def _get(path, body=None, timeout=5):
    req = urllib.request.Request(OLLAMA + path, data=json.dumps(body).encode() if body else None,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))


def ollama_info(model=MODEL):
    out = {'ollama_version': None, 'model': model, 'model_digest': None, 'model_details': None}
    try:
        out['ollama_version'] = _get('/api/version').get('version')
        tag = next((m for m in _get('/api/tags').get('models', []) if m.get('name') == model or m.get('model') == model), None)
        if tag:
            out['model_digest'] = tag.get('digest')
            out['model_details'] = tag.get('details')
    except Exception as e:
        out['ollama_error'] = str(e)
    return out


def smi(fields):
    """One nvidia-smi query for GPU 0, as a dict. Empty when there is no nvidia-smi."""
    try:
        r = subprocess.run(['nvidia-smi', '--query-gpu=' + ','.join(fields), '--format=csv,noheader,nounits', '-i', '0'],
                           capture_output=True, text=True, timeout=10, creationflags=0x08000000)   # no console window
        vals = [v.strip() for v in r.stdout.strip().split(',')]
        return dict(zip(fields, vals)) if len(vals) == len(fields) else {}
    except Exception:
        return {}


GPU_STATE = ['temperature.gpu', 'clocks.sm', 'clocks.mem', 'power.draw', 'power.limit', 'enforced.power.limit',
             'pstate', 'clocks_throttle_reasons.active']


def gpu_state():
    s = smi(GPU_STATE)
    if not s:   # older/newer drivers name the throttle field differently: try without it
        s = smi(GPU_STATE[:-1])
    return s


def versions(model=MODEL):
    try:
        import importlib.metadata as md
        zeus = md.version('zeus')
    except Exception:
        zeus = None
    try:
        wb = sys.getwindowsversion()
        win = '%d.%d.%d' % (wb.major, wb.minor, wb.build)
    except Exception:
        win = None
    g = smi(['name', 'driver_version', 'memory.total', 'vbios_version'])
    return dict(python=sys.version.split()[0], python_exe=sys.executable, zeus=zeus, os=platform.platform(),
                windows_build=win, gpu=g.get('name'), driver=g.get('driver_version'), vram_mib=g.get('memory.total'),
                vbios=g.get('vbios_version'), **ollama_info(model))
