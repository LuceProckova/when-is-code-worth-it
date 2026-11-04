"""One-prompt baseline: every test request goes to ONE model call with the same today line and tables.

  python run_baseline.py --backend ollama --model qwen2.5:7b --ids 1,7                  (condition oneprompt7b, laptop)
  python run_baseline.py --backend ollama --model qwen2.5:72b-instruct-q4_K_M --reps 30 (oneprompt72b, run ON the GPU box)
  python run_baseline.py --backend claude --model claude-sonnet-5-5 [--batch]            (claude, key from ANTHROPIC_API_KEY)
  python run_baseline.py --dry-prompt --ids 1                                            (print one filled prompt, no call)

Writes 11-Runs/<condition>-<date>.jsonl with the same keys as run_tests.py, so one analysis script reads both.
Claude goes through the Messages API directly (never the claude CLI, which would load her CLAUDE.md and memory).
No calendar access anywhere in this script: the result is only logged.
"""
import argparse
import csv
import datetime
import glob
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
import warnings

warnings.filterwarnings('ignore')
HERE = os.path.dirname(os.path.abspath(__file__))
REQUESTS = os.path.join(os.path.dirname(HERE), '10-Test-Data', 'requests.csv')
PROMPT = os.path.join(HERE, 'baseline_prompt.txt')
TABLES = r'C:\Users\lucyp\Desktop\All-My-Stuff\0-AI-Solutions\08-Master-Interface\03 - My Data\knowledge\thesis'
TIMEZONE = 'Europe/Bratislava'
# same as papertrail.ask_model (temperature 0, seed 1, JSON mode); num_ctx raised so the tables fit
OLLAMA_OPTS = {'temperature': 0, 'seed': 1, 'num_ctx': 8192, 'num_predict': 1024}
MAX_TOKENS = 1024
ANTHROPIC = 'https://api.anthropic.com/v1'


def today_line(today):
    """The one line every condition gets: 'Today is Saturday 7 November 2026, 10:00, Europe/Bratislava'."""
    d = datetime.datetime.strptime(today, '%Y-%m-%d %H:%M')
    return 'Today is %s %d %s, %s, %s.' % (d.strftime('%A'), d.day, d.strftime('%B %Y'), d.strftime('%H:%M'), TIMEZONE)


def tables_text(folder):
    """Every thesis table pasted as CSV under its file name, in a fixed order."""
    parts = []
    for p in sorted(glob.glob(os.path.join(folder, '*.csv'))):
        with open(p, encoding='utf-8-sig') as f:
            parts.append('Table %s:\n%s' % (os.path.basename(p), f.read().strip()))
    return '\n\n'.join(parts)


def fill(template, tables, row):
    return template.format(today_line=today_line(row['today']), tables=tables, request=row['request'])


def parse(text):
    """The model's JSON answer, or None. Strips code fences and any text around the outer braces."""
    if not text:
        return None
    t = re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip())
    try:
        out = json.loads(t)
    except ValueError:
        i, j = t.find('{'), t.rfind('}')
        if i < 0 or j <= i:
            return None
        try:
            out = json.loads(t[i:j + 1])
        except ValueError:
            return None
    if isinstance(out, dict):
        out.setdefault('events', [])
        out.setdefault('asks', [])
    return out


def gpu_energy():
    """Zeus reading the GPU's own energy counter, in joules (same as run_tests.py). None without an NVIDIA GPU."""
    try:
        from zeus.device import get_gpus
        g = get_gpus()
        return lambda: g.get_total_energy_consumption(0) / 1000
    except Exception:
        return None


def gpu_name():
    try:
        import pynvml
        pynvml.nvmlInit()
        n = pynvml.nvmlDeviceGetName(pynvml.nvmlDeviceGetHandleByIndex(0))
        return n.decode() if isinstance(n, bytes) else n
    except Exception:
        return None


def post(url, body, headers, timeout=600):
    req = urllib.request.Request(url, data=json.dumps(body).encode('utf-8'), headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))


def call_ollama(host, model, prompt):
    t0 = time.time()
    schema_file = os.path.join(HERE, 'baseline_schema.json')   # the answer's fixed shape (structured outputs), if made
    fmt = json.load(open(schema_file, encoding='utf-8')) if os.path.exists(schema_file) else 'json'
    r = post(host.rstrip('/') + '/api/generate', {'model': model, 'prompt': prompt, 'stream': False,
                                                   'format': fmt, 'options': OLLAMA_OPTS}, {'Content-Type': 'application/json'})
    step = {'id': 'oneprompt', 'kind': 'model', 'model': model, 'tokens': (r.get('prompt_eval_count') or 0) + (r.get('eval_count') or 0),
            'prompt_tokens': r.get('prompt_eval_count'), 'output_tokens': r.get('eval_count'),
            'prompt_eval_ns': r.get('prompt_eval_duration'), 'eval_ns': r.get('eval_duration'), 'load_ns': r.get('load_duration'),
            'ms': int((time.time() - t0) * 1000)}
    return r.get('response', ''), step


def claude_headers():
    key = os.environ.get('ANTHROPIC_API_KEY')
    kf = os.path.join(os.path.expanduser('~'), '.anthropic-key.txt')   # 4.11.26: the key is saved here by hand, so it never passes through a chat
    if not key and os.path.exists(kf):
        key = open(kf, encoding='utf-8-sig').read().strip()
    if not key:
        sys.exit('ANTHROPIC_API_KEY is not set')
    return {'x-api-key': key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'}


def claude_schema():
    """The same answer shape the Qwen runs get (baseline_schema.json), with every object closed, as structured outputs require."""
    def close(x):
        if isinstance(x, dict):
            x = {k: close(v) for k, v in x.items()}
            if x.get('type') == 'object':
                x['additionalProperties'] = False
            return x
        return [close(v) for v in x] if isinstance(x, list) else x
    return close(json.load(open(os.path.join(HERE, 'baseline_schema.json'), encoding='utf-8')))


def claude_params(model, prompt):
    # 4.11.26: Claude answers straight away like Qwen (thinking between_tools = off), held to the same answer
    # shape; Sonnet 5.5 refuses temperature, so sampling stays at the model default (noted in the method chapter)
    p = {'model': model, 'max_tokens': 4096,
         'output_config': {'format': {'type': 'json_schema', 'schema': claude_schema()}},
         'messages': [{'role': 'user', 'content': prompt}]}
    if model.startswith('claude-sonnet-5-5'):
        p['thinking'] = {'type': 'between_tools'}   # thinking off, Sonnet 5.5 only
    elif model.startswith('claude-haiku-5-5'):
        p['thinking'] = {'type': 'disabled'}   # thinking off, allowed on Haiku 5.5 at its default effort (4.11.26)
    else:   # Opus 5.5 cannot switch thinking off: least thinking it allows, room for it (4.11.26)
        p['output_config']['effort'] = 'low'
        p['max_tokens'] = 16000
    return p


def claude_step(msg, model, ms):
    u = msg.get('usage') or {}
    return {'id': 'oneprompt', 'kind': 'model', 'model': msg.get('model', model),
            'tokens': (u.get('input_tokens') or 0) + (u.get('output_tokens') or 0),
            'prompt_tokens': u.get('input_tokens'), 'output_tokens': u.get('output_tokens'),
            'stop_reason': msg.get('stop_reason'), 'ms': ms}


def claude_text(msg):
    return ''.join(b.get('text', '') for b in msg.get('content', []) if b.get('type') == 'text')


def call_claude(model, prompt):
    t0 = time.time()
    for attempt in range(5):
        try:
            msg = post(ANTHROPIC + '/messages', claude_params(model, prompt), claude_headers())
            break
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 529) and attempt < 4:
                time.sleep(2 ** attempt * 5)
                continue
            raise RuntimeError('HTTP %s %s' % (e.code, e.read().decode('utf-8', 'replace')[:500]))
    return claude_text(msg), claude_step(msg, model, int((time.time() - t0) * 1000))


def claude_batch(model, jobs):
    """Message Batches API (half price). jobs: list of (custom_id, prompt). Returns {custom_id: message or error dict}."""
    h = claude_headers()
    b = post(ANTHROPIC + '/messages/batches', {'requests': [{'custom_id': cid, 'params': claude_params(model, p)} for cid, p in jobs]}, h)
    print('batch', b['id'], 'submitted with', len(jobs), 'requests')
    while b.get('processing_status') != 'ended':
        time.sleep(60)
        with urllib.request.urlopen(urllib.request.Request(ANTHROPIC + '/messages/batches/' + b['id'], headers=h)) as r:
            b = json.loads(r.read().decode('utf-8'))
        print(time.strftime('%H:%M:%S'), b.get('request_counts'))
    out = {}
    with urllib.request.urlopen(urllib.request.Request(b['results_url'], headers=h)) as r:
        for line in r.read().decode('utf-8').splitlines():
            if line.strip():
                x = json.loads(line)
                out[x['custom_id']] = x['result']
    return b['id'], out


def condition_name(a):
    if a.backend == 'claude':
        return 'claude'
    m = re.search(r'(\d+)b', a.model)
    return 'oneprompt%sb' % m.group(1) if m else 'oneprompt'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--backend', default='ollama', choices=['ollama', 'claude'])
    ap.add_argument('--model', default=None, help='ollama: qwen2.5:7b or qwen2.5:72b-instruct-q4_K_M; claude: claude-sonnet-5-5')
    ap.add_argument('--host', default='http://127.0.0.1:11434', help='Ollama server (run the script ON the GPU box for energy)')
    ap.add_argument('--ids', default='')
    ap.add_argument('--reps', type=int, default=1)
    ap.add_argument('--seed', type=int, default=2610, help='request order per repeat, the same rule as run_tests.py')
    ap.add_argument('--batch', action='store_true', help='claude only: send everything as one Message Batch')
    ap.add_argument('--dry-prompt', action='store_true', help='print one filled prompt and stop')
    ap.add_argument('--tables', default=TABLES)
    ap.add_argument('--requests', default=REQUESTS)
    ap.add_argument('--out', default=None)
    ap.add_argument('--resume', action='store_true', help='skip request and repeat pairs already in --out (after a stop)')
    ap.add_argument('--echo-json', action='store_true', help='also print every record as one line starting with @@ (the dashboard reads this over ssh)')
    a = ap.parse_args()
    a.model = a.model or ('claude-sonnet-5-5' if a.backend == 'claude' else 'qwen2.5:7b')

    rows = list(csv.DictReader(open(a.requests, encoding='utf-8-sig')))
    if a.ids:
        want = {x.strip() for x in a.ids.split(',')}
        rows = [r for r in rows if r['id'] in want]
    template = open(PROMPT, encoding='utf-8').read()
    tables = tables_text(a.tables)
    if a.dry_prompt:
        print(fill(template, tables, rows[0]))
        return

    cond = condition_name(a)
    out = a.out or os.path.join(HERE, '%s-%s.jsonl' % (cond, time.strftime('%Y-%m-%d')))
    local = any(h in a.host for h in ('127.0.0.1', 'localhost'))
    energy = gpu_energy() if a.backend == 'ollama' and local else None
    hardware = (gpu_name() or 'unknown GPU') if a.backend == 'ollama' and local else ('Anthropic API' if a.backend == 'claude' else 'remote ' + a.host)

    model_info = {'prompt_file_sha256': __import__('hashlib').sha256(template.encode('utf-8')).hexdigest()}
    if a.backend == 'ollama':   # quantization and digest on record for every version (Menzel 31.10.26)
        try:
            tag = next((m for m in json.loads(urllib.request.urlopen(a.host.rstrip('/') + '/api/tags', timeout=10).read())['models']
                        if a.model in (m.get('name'), m.get('model'))), {})
            model_info.update(digest=tag.get('digest'), details=tag.get('details'), options=OLLAMA_OPTS)
        except Exception as e:
            model_info['error'] = str(e)
    else:
        model_info.update(details={'quantization_level': 'not disclosed (hosted API)'}, max_tokens=MAX_TOKENS, temperature=0)

    def log(r, rep, text, step, seconds, joules, error=None, extra=None):
        result = parse(text)
        step = dict(step, ok=result is not None and error is None, out=text)
        rec = {'time': time.strftime('%Y-%m-%dT%H:%M:%S'), 'condition': cond, 'id': r['id'], 'rep': rep, 'dry': True,
               'today': r['today'], 'request': r['request'], 'result': result,
               'ok': step['ok'], 'error': error or (None if result is not None else 'answer is not JSON'),
               'tokens': step.get('tokens') or 0, 'seconds': seconds,
               'gpu_joules': joules, 'steps': [step], 'backend': a.backend, 'model': a.model, 'hardware': hardware,
               'model_info': model_info}
        if extra:
            rec.update(extra)
        with open(out, 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
        if a.echo_json:
            print('@@' + json.dumps(rec, ensure_ascii=False), flush=True)
        print(r['id'], rep, '%.1fs' % (seconds or 0), joules, 'J', '| ERROR ' + str(rec['error']) if rec['error'] else '',
              json.dumps(result, ensure_ascii=False)[:400])

    if a.backend == 'claude' and a.batch:
        done_b = set()   # --resume: repeats already in --out are not sent again (4.11.26)
        if a.resume and os.path.exists(out):
            for line in open(out, encoding='utf-8'):
                dd = json.loads(line) if line.strip() else {}
                if 'result' in dd and not dd.get('error'):
                    done_b.add((str(dd['id']), int(dd['rep'])))
        jobs = [('%s-%d' % (r['id'], rep), fill(template, tables, r)) for rep in range(1, a.reps + 1) for r in rows if (r['id'], rep) not in done_b]
        bid, res = claude_batch(a.model, jobs)
        for rep in range(1, a.reps + 1):
            for r in rows:
                if (r['id'], rep) in done_b:
                    continue
                x = res.get('%s-%d' % (r['id'], rep), {'type': 'missing'})
                if x.get('type') == 'succeeded':
                    log(r, rep, claude_text(x['message']), claude_step(x['message'], a.model, None), None, None, extra={'batch_id': bid})
                else:
                    log(r, rep, '', {'id': 'oneprompt', 'kind': 'model', 'model': a.model, 'tokens': 0}, None, None,
                        error=json.dumps(x)[:500], extra={'batch_id': bid})
        return

    done_pairs = set()
    if a.resume and os.path.exists(out):
        for line in open(out, encoding='utf-8'):
            try:
                d = json.loads(line)
                done_pairs.add((str(d['id']), int(d['rep'])))
            except (ValueError, KeyError):
                pass
    if a.backend == 'ollama':
        # warm-up: the first call loads the model into the GPU, it is not part of any run
        call_ollama(a.host, a.model, fill(template, tables, rows[0]))
    for rep in range(1, a.reps + 1):
        order = rows[:]
        random.Random(a.seed * 1000 + rep * 10).shuffle(order)   # same shuffle as run_tests.py (ci 0): GPU warm-up and heat do not always hit the same requests (4.11.26)
        for r in order:
            if (r['id'], rep) in done_pairs:
                continue
            prompt = fill(template, tables, r)
            print('@@now', cond, rep, r['id'], flush=True)
            e0, t0 = (energy() if energy else None), time.time()
            try:
                text, step = call_ollama(a.host, a.model, prompt) if a.backend == 'ollama' else call_claude(a.model, prompt)
                err = None
            except Exception as e:
                text, step, err = '', {'id': 'oneprompt', 'kind': 'model', 'model': a.model, 'tokens': 0}, str(e)
            t1, e1 = time.time(), (energy() if energy else None)
            log(r, rep, text, step, round(t1 - t0, 3), round(e1 - e0, 2) if energy else None, error=err)


if __name__ == '__main__':
    main()
