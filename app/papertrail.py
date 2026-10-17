"""PaperTrAIl inside the Master Interface: the router, the step runner and the run log.

A request is first split into its jobs. Each job goes to the small local model, which must pick one workflow or answer
"none". Code checks the answer: a real workflow id runs that workflow, "none" or anything else goes
to Claude (in auto). Every request is logged to My Data/runs/<date>.jsonl, which is the thesis data.

A workflow is a JSON file in My Data/workflows/. Each step is done by a model, by code or by a human:
  model  a prompt for the local model, answered as JSON
  code   a function from CODE_STEPS below
  human  needs the person (not built yet, the run stops and says so)
"""
import json
import re
import os
import shutil
import subprocess
import time
import urllib.request

OLLAMA = 'http://127.0.0.1:11434/api/generate'
SMALL = 'qwen2.5:7b'
SKIP = {'.git', 'node_modules', '__pycache__', '$RECYCLE.BIN', 'System Volume Information',
        '.venv', 'venv', 'build', '.history', 'Archive', '00-Archive'}

DATA_DIR = None   # set by server.py
ROOT = None       # set per request from settings


# ---------- models ----------

def ask_small(prompt, model=SMALL):
    """One call to a small model, temperature 0, JSON out. Returns (dict, tokens, ms).
    A model added through an API goes to its OpenAI-style endpoint, anything else to Ollama."""
    api = api_model(model)
    if api:
        return ask_api(prompt, api)
    body = json.dumps({'model': model, 'prompt': prompt, 'stream': False, 'format': 'json',
                       'options': {'temperature': 0, 'seed': 1}}).encode('utf-8')
    t0 = time.time()
    req = urllib.request.Request(OLLAMA, data=body, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=120) as r:
        res = json.loads(r.read().decode('utf-8'))
    ms = int((time.time() - t0) * 1000)
    tokens = (res.get('prompt_eval_count') or 0) + (res.get('eval_count') or 0)
    try:
        out = json.loads(res.get('response') or '{}')
    except ValueError:
        out = {}
    return out, tokens, ms


def ask_api(prompt, cfg):
    """An OpenAI-compatible chat endpoint (OpenAI, Mistral, OpenRouter, a local LM Studio...)."""
    key = load_secrets().get(cfg['name'], '')
    body = json.dumps({'model': cfg['model'], 'temperature': 0, 'response_format': {'type': 'json_object'},
                       'messages': [{'role': 'user', 'content': prompt}]}).encode('utf-8')
    t0 = time.time()
    req = urllib.request.Request(cfg['base_url'].rstrip('/') + '/chat/completions', data=body,
                                 headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key})
    with urllib.request.urlopen(req, timeout=120) as r:
        res = json.loads(r.read().decode('utf-8'))
    ms = int((time.time() - t0) * 1000)
    tokens = (res.get('usage') or {}).get('total_tokens') or 0
    try:
        out = json.loads(res['choices'][0]['message']['content'] or '{}')
    except (ValueError, KeyError, IndexError):
        out = {}
    return out, tokens, ms


# ---------- models: the ones on this computer and the ones added through an API ----------

def secrets_path():
    return os.path.join(DATA_DIR, 'secrets.json')


def load_secrets():
    try:
        with open(secrets_path(), encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def api_model(name):
    for m in load_meta().get('models', []):
        if m.get('name') == name and m.get('kind') == 'api':
            return m
    return None


def list_models():
    out = []
    try:
        with urllib.request.urlopen(OLLAMA.replace('/api/generate', '/api/tags'), timeout=3) as r:
            out = [{'name': m['name'], 'kind': 'local'} for m in json.loads(r.read().decode('utf-8')).get('models', [])]
    except Exception:
        pass
    out += [{'name': m['name'], 'kind': 'api'} for m in load_meta().get('models', []) if m.get('kind') == 'api']
    return out


def add_model(body):
    """kind api: name, base_url, model, key (the key goes to secrets.json, never the meta file).
    kind local: name and the path of a .gguf file, which Ollama imports with ollama create."""
    name = (body.get('name') or '').strip()
    if not name:
        return False, 'give the model a name'
    if body.get('kind') == 'api':
        if not body.get('base_url') or not body.get('model'):
            return False, 'the API address and the model id are both needed'
        m = load_meta()
        m['models'] = [x for x in m.get('models', []) if x.get('name') != name] + [
            {'name': name, 'kind': 'api', 'base_url': body['base_url'].strip(), 'model': body['model'].strip()}]
        save_meta(m)
        sec = load_secrets()
        sec[name] = (body.get('key') or '').strip()
        with open(secrets_path(), 'w', encoding='utf-8') as f:
            json.dump(sec, f, indent=2)
        return True, 'added'
    path = (body.get('path') or '').strip().strip('"')
    if not os.path.isfile(path):
        return False, 'no file at that path'
    exe = shutil.which('ollama')
    if not exe:
        return False, 'Ollama is not installed'
    mf = os.path.join(DATA_DIR, 'Modelfile.tmp')
    with open(mf, 'w', encoding='utf-8') as f:
        f.write('FROM "%s"\n' % path)
    r = subprocess.run([exe, 'create', name, '-f', mf], capture_output=True, text=True, encoding='utf-8', timeout=900)
    try:
        os.remove(mf)
    except OSError:
        pass
    return (r.returncode == 0), (r.stderr or r.stdout or '').strip()[-300:] or 'added'


def ask_claude(text):
    """The big brain through the claude CLI, answer only, no file changes (it asks before writes)."""
    exe = shutil.which('claude')
    if not exe:
        return {'ok': False, 'answer': 'The claude CLI is not on this computer.'}, 0
    t0 = time.time()
    try:
        r = subprocess.run([exe, '-p', text, '--output-format', 'json'], capture_output=True,
                           text=True, encoding='utf-8', timeout=300, cwd=ROOT)
        ms = int((time.time() - t0) * 1000)
        try:
            res = json.loads(r.stdout)
            return {'ok': not res.get('is_error'), 'answer': res.get('result', ''),
                    'cost_usd': res.get('total_cost_usd')}, ms
        except ValueError:
            return {'ok': r.returncode == 0, 'answer': (r.stdout or r.stderr).strip()}, ms
    except subprocess.TimeoutExpired:
        return {'ok': False, 'answer': 'Claude took longer than 5 minutes, stopped.'}, 300000


# ---------- workflows ----------

def wf_dir():
    return os.path.join(DATA_DIR, 'workflows')


def load_workflows():
    out = []
    try:
        names = sorted(os.listdir(wf_dir()))
    except OSError:
        return out
    for n in names:
        if n.endswith('.json'):
            try:
                with open(os.path.join(wf_dir(), n), encoding='utf-8') as f:
                    out.append(json.load(f))
            except Exception:
                pass
    return out


# ---------- designing flows: the catalogue, the checks, saving, drafting with Claude ----------

def catalog():
    """Everything a flow can be built from: code steps (with what they do), tools that are on, tables."""
    load_code_steps()
    import connections
    code = {n: ((f.__doc__ or '').strip().split('\n')[0]) for n, f in CODE_STEPS.items()}
    tools = {}
    for c in connections.all_connections():
        if c.get('enabled', True):
            for n, t in c.get('tools', {}).items():
                tools.setdefault(c['id'], {})[n] = {'permission': t.get('permission'), 'kind': t.get('kind'), 'description': t.get('description', '')}
    return {'code': code, 'tools': tools, 'tables': [k['id'] for k in list_knowledge()]}


def check_flow(wf, cat=None):
    """The double check: what in this flow would not work or does nothing."""
    cat = cat or catalog()
    probs, made, used = [], {'input'}, set()
    tables = set(cat['tables'])
    steps = wf.get('steps', [])
    if not steps or steps[0].get('kind') != 'model':
        probs.append('The first step should be the intake (a model step reading your message)')
    for st in steps:
        name = st.get('title') or st.get('id')
        for v in [st.get('in')] + list(st.get('reads') or []):
            if v and v not in made:
                probs.append('%s needs "%s", which no earlier step makes' % (name, v))
            if v:
                used.add(v)
        for t in st.get('uses') or []:
            if t not in tables:
                probs.append('%s reads the table %s, which does not exist' % (name, t))
        if st.get('kind') == 'code' and st.get('fn') not in cat['code']:
            probs.append('%s uses the function "%s", which is not built yet' % (name, st.get('fn')))
        if st.get('kind') == 'tool':
            tl = cat['tools'].get(st.get('connection'), {}).get(st.get('tool'))
            if not tl:
                probs.append('%s uses %s %s, which is not connected or is off' % (name, st.get('connection'), st.get('tool')))
            elif tl.get('permission') == 'off':
                probs.append('%s uses %s, which is set to Off in Connections' % (name, st.get('tool')))
        if st.get('kind') == 'model' and not st.get('prompt'):
            probs.append('%s has no prompt' % name)
        if st.get('out'):
            made.add(st['out'])
        made.update(st.get('also_out') or [])
    for st in steps[:-1]:
        if st.get('out') and st['out'] not in used and st.get('role') != 'act' and not st.get('learns'):
            probs.append('Nothing uses what %s makes' % (st.get('title') or st.get('id')))
    return probs


def save_flow(wf):
    """Save a flow you edited. The version before goes to Archive, so nothing is lost."""
    import shutil as sh
    path = os.path.join(wf_dir(), wf['id'] + '.json')
    if os.path.exists(path):
        arch = os.path.join(os.path.dirname(DATA_DIR), 'Archive')
        os.makedirs(arch, exist_ok=True)
        sh.copy(path, os.path.join(arch, '%s (before edit %s).json' % (wf['id'], time.strftime('%d.%m.%y %H%M'))))
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(wf, f, indent=2, ensure_ascii=False)
    return check_flow(wf)


def draft_flow(goal):
    """Claude designs a flow with the decomposition skill, from what exists. It is saved as a draft."""
    skill = ''
    try:
        with open(os.path.expanduser('~/.claude/skills/flow-decomposition/SKILL.md'), encoding='utf-8') as f:
            skill = f.read()
    except OSError:
        pass
    example = next((w for w in load_workflows() if w['id'] == 'calendar-create-events'), {})
    meta = load_meta()
    prompt = ('Design a PaperTrAIl flow for this job: %s\n\nFollow this method:\n%s\n\nWhat exists (use only these; '
              'if a check needs a code function that does not exist, still add the step with a clear fn name and it will be flagged '
              'as not built yet):\n%s\n\nNaming rules for flows: %s\n\nAn existing flow as an example of the JSON:\n%s\n\n'
              'Reply with the flow JSON only: {"id","name","folder","description","examples","patterns","steps":[...]}. '
              'Every step needs id, title (the question it answers, no explanation), kind, in/reads/uses/out as needed.'
              % (goal, skill, json.dumps(catalog(), ensure_ascii=False), meta.get('naming_rules', ''),
                 json.dumps(example, ensure_ascii=False)[:9000]))
    raw = ask_claude_json(prompt)
    if not raw.get('steps'):
        return None, ['Claude did not return a flow']
    raw['id'] = re.sub(r'[^a-z0-9]+', '-', (raw.get('id') or raw.get('name') or 'new-flow').lower()).strip('-')
    raw['draft'] = True
    raw['goal'] = goal
    return raw, save_flow(raw)


def route_prompt(text, wfs):
    lines = '\n'.join('- %s: %s (e.g. %s)' % (w['id'], w['description'], '; '.join(w.get('examples', [])[:3]))
                      for w in wfs)
    return ('You are a router. Pick the ONE workflow that does exactly what the request asks, '
            'or "none" if no workflow fits.\n\nWorkflows:\n%s\n\nRequest: %s\n\n'
            'Answer as JSON {"workflow": "<id or none>"}' % (lines, text))


# ---------- code steps ----------

def search_files(term, ctx=None):
    """Files under the root whose name holds every word of the term, newest first, top 5."""
    words = [w for w in (term or '').lower().replace('-', ' ').replace('_', ' ').split() if len(w) > 1]
    if not words:
        return []
    hits = []
    deadline = time.time() + 6
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP and not d.startswith('.')]
        for fn in filenames:
            name = fn.lower().replace('-', ' ').replace('_', ' ')
            if all(w in name for w in words):
                full = os.path.join(dirpath, fn)
                try:
                    mt = os.path.getmtime(full)
                except OSError:
                    mt = 0
                hits.append((mt, os.path.relpath(full, ROOT).replace('\\', '/')))
        if time.time() > deadline:
            break
    hits.sort(reverse=True)
    return [h[1] for h in hits[:5]]


def open_first(hits, ctx=None):
    if not hits:
        raise ValueError('no file with that name')
    return {'open': hits[0], 'say': 'Opened ' + hits[0].split('/')[-1] +
            ('' if len(hits) == 1 else ', %d more matches' % (len(hits) - 1)), 'others': hits[1:]}


CODE_STEPS = {'search_files': search_files, 'open_first': open_first}


def load_code_steps():
    """Every steps/<area>.py adds its STEPS, so a new area of flows is a new file, nothing else."""
    import importlib
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'steps')
    for n in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if n.endswith('.py') and not n.startswith('_'):
            m = importlib.import_module('steps.' + n[:-3])
            if hasattr(m, 'KNOW'):
                m.KNOW = know_dir()
            CODE_STEPS.update(m.STEPS)


# ---------- knowledge: rules and tables the flows read (My Data/knowledge) ----------

def know_dir():
    d = os.path.join(DATA_DIR, 'knowledge')
    os.makedirs(d, exist_ok=True)
    return d


KNOW_TYPES = ('.csv', '.md', '.json', '.txt')


def list_knowledge():
    """Every file in My Data/knowledge, each area a folder, id = area/name without the extension."""
    out = []
    for dirpath, _, files in os.walk(know_dir()):
        for n in sorted(files):
            if n.endswith(KNOW_TYPES):
                rel = os.path.relpath(os.path.join(dirpath, n), know_dir()).replace('\\', '/')
                out.append({'id': rel.rsplit('.', 1)[0], 'file': rel, 'size': os.path.getsize(os.path.join(dirpath, n))})
    return out


def read_knowledge(kid):
    for k in list_knowledge():
        if k['id'] == kid:
            with open(os.path.join(know_dir(), k['file']), encoding='utf-8-sig') as f:
                return f.read()
    return ''


def fill(prompt, text, ctx, uses):
    """{input} is the request, {today} the date, {knowledge} the files the step uses, {x} anything in ctx."""
    from datetime import datetime
    out = prompt.replace('{input}', text).replace('{today}', datetime.now().strftime('%A %Y-%m-%d %H:%M'))
    out = out.replace('{knowledge}', '\n\n'.join(read_knowledge(k) for k in uses or []))
    for k, v in ctx.items():
        if '{' + k + '}' in out:
            out = out.replace('{' + k + '}', v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
    return out


# ---------- runner ----------

def short(v, limit=3000):
    """What the trail keeps of a step's output: all of it when small, a count and a sample when big."""
    txt = json.dumps(v, ensure_ascii=False)
    if len(txt) <= limit:
        return v
    if isinstance(v, dict) and isinstance(v.get('events'), list):
        return {'events': len(v['events']), 'first': v['events'][:3]}
    if isinstance(v, list):
        return {'items': len(v), 'first': v[:3]}
    return txt[:limit] + '...'


# ---------- grounding: every value in an intake table must come from the message ----------

STOP = {'the', 'and', 'for', 'with', 'from', 'that', 'this', 'into', 'onto', 'have', 'will', 'are', 'was', 'you', 'your', 'my', 'our',
        'can', 'please', 'put', 'add', 'book', 'calendar'}
TIME = re.compile(r'\b(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)?\b', re.I)


def _times(text):
    out = set()
    for h, m, ap in TIME.findall(text or ''):
        h = int(h)
        if ap.lower() == 'pm' and h < 12:
            h += 12
        if h < 24:
            out.add('%02d:%02d' % (h, int(m or 0)))
    return out


def words_in(value, text):
    """Is this value really in the text? Times compare as times, words by their stems, most of them must be there."""
    v = ' '.join(value) if isinstance(value, list) else str(value)
    vt = _times(v)
    if vt and re.fullmatch(r'[\d:.\sapmAPM]+', v.strip()):
        return bool(vt & _times(text))
    tw = re.findall(r'[a-zà-ž0-9]+', (text or '').lower())
    stems = {w[:5] for w in tw}
    vw = [w for w in re.findall(r'[a-zà-ž0-9]+', v.lower()) if len(w) > 2 and w not in STOP]
    if not vw:
        return True
    return sum(1 for w in vw if w[:5] in stems) / len(vw) >= 0.6


def ground(rows, text, fields):
    """One rule for every field of every flow. A value the person said must be in the message. A value the
    model inferred (an action, how often, a priority...) must come with the words it was inferred from,
    and those words must be in the message. Anything else is cleared. A field can also name a table whose
    values it may not be (a calendar name is not a place)."""
    fixes = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        ev = r.get('evidence') if isinstance(r.get('evidence'), dict) else {}
        for f, spec in fields.items():
            v = r.get(f)
            if v in (None, '', False, [], 'false') or str(v).lower() in [str(spec.get('default', '')).lower()] + spec.get('ignore', []):
                if str(v).lower() in spec.get('ignore', []):
                    r[f] = spec.get('default', '')
                continue
            if spec.get('kind') == 'said':
                ok = words_in(v, text)
            else:   # inferred: its own words are in the message, or the words it was inferred from are
                ok = words_in(v, text) or (bool(ev.get(f)) and words_in(ev.get(f), text))
            if not ok and spec.get('type'):
                ok = said_as_type(v, spec['type'], text)   # said another way: "an hour" is 60 minutes, "Mondays" is weekly
            if ok and spec.get('type'):
                v2 = as_type(v, spec['type'])
                ok = v2 is not None
                if ok:
                    r[f] = v = v2
            if ok and spec.get('not_in_table'):
                tbl, col = spec['not_in_table'].split(':')
                ok = str(v).strip().lower() not in {x.lower() for x in table_column(tbl, col)}
            if not ok:
                fixes.append('%s "%s" is not in what you wrote' % (f, ' '.join(v) if isinstance(v, list) else v))
                r[f] = spec.get('default', '')
        r.pop('evidence', None)
        # the main field holds only the thing itself: words that belong to other fields come out of it
        for f, spec in fields.items():
            if spec.get('main') and isinstance(r.get(f), str):
                v = r[f]
                for g, other in fields.items():
                    ov = r.get(g)
                    if g != f and other.get('kind') == 'said' and isinstance(ov, str) and ov and ov.lower() != v.lower():
                        v = re.sub(r'\s*\b(on|at|in|for|from|till|until|to|with)?\s*' + re.escape(ov) + r'\b', '', v, flags=re.I)
                v = re.sub(r'\s*\b(on|at|in|for)\s+(\w+day|\w+days|\d{1,2}([:.]\d{2})?\s*(am|pm)?|an? (hour|half hour)|half an hour|\d+ (minutes|mins|hours?))\b', '', v, flags=re.I)
                r[f] = re.sub(r'\s+', ' ', v).strip(' ,') or r[f]
    return rows, fixes


WEEKDAY = r'(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)s?'
DURATION = re.compile(r'(quarter of an hour|half an hour|half hour|an hour and a half|an hour|one hour|a hour|two hours|three hours|\d+(?:[.,]\d+)?\s*(?:h|hr|hrs|hours?|m|min|mins|minutes?))\b', re.I)
REPEAT = re.compile(r'\b(every|each|weekly|daily|monthly|' + WEEKDAY[1:-3] + r')s\b|\b(every|each|weekly|daily|monthly)\b', re.I)


def said_as_type(v, kind, text):
    """Is this value in the message in another form its type allows?"""
    if kind == 'minutes':
        want = as_type(v, 'minutes')
        return want is not None and any(as_type(m.group(1), 'minutes') == want for m in DURATION.finditer(text or ''))
    if kind == 'repeat':
        return bool(REPEAT.search(text or ''))
    return False


def as_type(v, kind):
    """The value as its type, or None when it is not that kind of thing at all."""
    s = (' '.join(v) if isinstance(v, list) else str(v)).strip().lower()
    if kind == 'day':
        if re.fullmatch(r'(this |next |on )?' + WEEKDAY, s):
            return re.sub(r's$', '', s) if re.fullmatch(r'(this |next |on )?' + WEEKDAY, s) and s.endswith('days') else s
        if re.fullmatch(r'today|tomorrow|tonight|day after tomorrow|in \d+ (day|days|week|weeks)|\d{1,2}\.\s*\d{1,2}\.?(\s*\d{2,4})?|\d{4}-\d{1,2}-\d{1,2}|\d{1,2}(st|nd|rd|th)?( of)? [a-z]+|[a-z]+ \d{1,2}(st|nd|rd|th)?', s):
            return s
        return None
    if kind == 'time':
        t = _times(s)
        return sorted(t)[0] if t else None
    if kind == 'minutes':
        if s.isdigit():
            return int(s)
        words = {'an hour': 60, 'one hour': 60, 'a hour': 60, 'half an hour': 30, 'half hour': 30, 'quarter of an hour': 15,
                 'an hour and a half': 90, 'two hours': 120, 'three hours': 180}
        if s in words:
            return words[s]
        m = re.fullmatch(r'(\d+(?:[.,]\d+)?)\s*(h|hr|hrs|hour|hours|m|min|mins|minute|minutes)', s)
        if m:
            n = float(m.group(1).replace(',', '.'))
            return int(n * 60) if m.group(2).startswith('h') else int(n)
        return None
    return v


def table_column(table, col):
    import csv
    p = os.path.join(know_dir(), table + '.csv')
    try:
        with open(p, encoding='utf-8-sig', newline='') as fh:
            return [row.get(col, '').strip() for row in csv.DictReader(fh)]
    except OSError:
        return []


def usable(val, required=None):
    """An answer is usable when it is there and every row has the fields the flow needs."""
    if val in (None, '', []):
        return False
    rows = val if isinstance(val, list) else [val]
    return all(isinstance(r, dict) and all(str(r.get(k) or '').strip() for k in (required or [])) for r in rows)


def ask_claude_json(prompt):
    ans, _ = ask_claude(prompt + '\n\nReply with the JSON only, nothing else, and do not use any tools.')
    txt = (ans.get('answer') or '').strip()
    a, b = txt.find('{'), txt.rfind('}')
    try:
        return json.loads(txt[a:b + 1]) if a >= 0 else {}
    except ValueError:
        return {}


def run_steps(wf, text, dry=False):
    """Run a workflow step by step, yielding each step's record as soon as it finishes.
    dry: write tools are not called, the step shows what it would have sent."""
    import connections
    load_code_steps()
    ctx = {'input': text, 'dry': dry}
    for st in wf['steps']:
        t0 = time.time()
        rec = {'id': st['id'], 'kind': st['kind'], 'tokens': 0}
        try:
            if st['kind'] == 'model':
                prompt = fill(st['prompt'], text, ctx, st.get('uses'))
                out, tok, _ = ask_small(prompt)
                val = out.get(st['out'])
                rec.update(tokens=tok, model=SMALL)
                if not usable(val, st.get('required')) and st.get('fallback') == 'claude':
                    # the small model could not do it: the big one gets the same question once
                    val = ask_claude_json(prompt).get(st['out'])
                    rec.update(model='claude', fell_back=True)
                if not usable(val, st.get('required')):
                    raise ValueError('the model gave no usable ' + st['out'])
                fixes = []
                if st.get('fields'):
                    val, fixes = ground(val, text, st['fields'])
                if st.get('check'):
                    ctx.pop('_note', None)
                    val = CODE_STEPS[st['check']](val, ctx)
                    ctx.pop('_note', None)
                rec['fixes'] = fixes + (ctx.get('intake_fixes') or [])
            elif st['kind'] == 'code':
                ctx.pop('_note', None)
                val = CODE_STEPS[st['fn']](ctx.get(st.get('in')), ctx)
                if ctx.get('_note'):
                    rec['note'] = ctx.pop('_note')
            elif st.get('when') and not ctx.get(st['when']):
                # only runs when there is something for it, e.g. moving lunch
                rec.update(ok=True, skipped=True, out='not needed this time', ms=0)
                ctx[st['out']] = None
                yield rec
                continue
            elif st['kind'] == 'tool':
                c = connections.load(st['connection'])
                perm = c.get('tools', {}).get(st['tool'], {}).get('permission', 'off')
                kind = c.get('tools', {}).get(st['tool'], {}).get('kind', 'write')
                if st.get('per'):
                    # one call per distinct value in the rows, e.g. read each date the events fall on
                    vals = sorted({r.get(st['per']) for r in (ctx.get(st['in']) or []) if r.get(st['per'])})
                    items = [{st['per']: v} for v in vals]
                elif st.get('each'):
                    items = ctx.get(st['in']) or []
                else:
                    items = [ctx.get(st['in']) if st.get('in') else st.get('args', {})]
                rec['args'] = items if st.get('per') else None
                if dry and kind == 'write':
                    val = {'dry_run': True, 'would_send': items}
                elif perm == 'ask' and st['tool'] not in ctx.get('approved', set()):
                    raise PermissionError(st['tool'] + ' is set to Ask in Connections')
                else:
                    res = [connections.call(st['connection'], st['tool'], a or {}) for a in items]
                    if st.get('per'):
                        val = [x for r in res for x in (r if isinstance(r, list) else [r])]
                    else:
                        val = res if st.get('each') else res[0]
            else:
                rec.update(ok=False, ms=0, out='needs a person, not built yet')
                yield rec
                return
            ctx[st['out']] = val
            rec.update(ok=True, out=short(val), ms=int((time.time() - t0) * 1000))
            yield rec
        except Exception as e:
            rec.update(ok=False, out=str(e), ms=int((time.time() - t0) * 1000))
            if hasattr(e, 'questions'):   # a step that needs you: the flow waits for your answers
                rec['ask'] = e.questions
            yield rec
            return


def log_run(rec):
    d = os.path.join(DATA_DIR, 'runs')
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, time.strftime('%Y-%m-%d') + '.jsonl'), 'a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')


MODIFIER = re.compile(r'^\s*(and\s+|but\s+)?(please\s+)?(book (it |them )?anyway|anyway|even if .*|(high|low|normal|top) priority|priority .*|urgent|because .*|move lunch to .*|keep lunch.*|add another|on (my )?[\w -]+ calendar|in (my )?[\w -]+ calendar)\s*[.!]?\s*$', re.I)


def split_request(text):
    """One request can hold several jobs. The small model lists them in the user's own words,
    one job comes back as the original text so nothing gets reworded."""
    try:
        out, tok, ms = ask_small(
            'Split this request into the separate jobs the user wants done. A job is one complete thing '
            'to do. Details like which calendar, when, where, who, how long, why (because...), priority '
            'or "book anyway" belong to the job they '
            "describe and are never a job of their own. Keep each job in the user's own words. If it is "
            "only one job, return just that one.\n\nExamples:\n"
            '"open my notes and tell me what RAG is" -> ["open my notes", "tell me what RAG is"]\n'
            '"lunch at Aupark Friday 13:00 on Friends" -> ["lunch at Aupark Friday 13:00 on Friends"]\n'
            '"yoga Monday 7:00 because my back hurts, high priority" -> ["yoga Monday 7:00 because my back hurts, high priority"]\n'
            '"add yoga Monday at 7 and find the EBA deck" -> ["add yoga Monday at 7", "find the EBA deck"]\n\n'
            "Request: %s\n\n"
            'Answer as JSON {"jobs": ["...", "..."]}' % text)
        jobs = [j.strip() for j in (out.get('jobs') or []) if isinstance(j, str) and j.strip()]
        # a "job" that is only a detail (book anyway, a priority, a reason) belongs to the job before it,
        # checked in code because the small model keeps splitting these off
        merged = []
        for j in jobs:
            if merged and MODIFIER.match(j):
                merged[-1] = merged[-1].rstrip(' ,.') + ', ' + j
            else:
                merged.append(j)
        return (merged if len(merged) > 1 else [text]), {'tokens': tok, 'ms': ms}
    except Exception as e:
        return [text], {'error': str(e)}


def handle_stream(text, brain='auto', dry=False):
    """Split, route and run one request, yielding events the interface animates as they happen."""
    req = time.strftime('%Y%m%d%H%M%S')
    wfs = [w for w in load_workflows() if not w.get('draft')]   # a draft never runs until approved
    ids = {w['id']: w for w in wfs}
    if brain == 'claude':
        parts, split = [text], None
    else:
        parts, split = split_request(text)
    yield {'ev': 'parts', 'parts': parts, 'split': split}
    for i, part in enumerate(parts):
        rec = {'time': time.strftime('%Y-%m-%dT%H:%M:%S'), 'req': req, 'request': text, 'text': part, 'brain': brain, 'dry': dry}
        if split and len(parts) > 1:
            rec['split'] = split
        picked = None
        # code first: a flow whose patterns match, and only that one, is picked without a model
        hits = [w for w in wfs if any(re.search(pt, part, re.I) for pt in w.get('patterns', []))]
        if brain != 'claude' and len(hits) == 1:
            picked = hits[0]
            rec['router'] = {'model': 'code', 'answer': picked['id'], 'tokens': 0, 'ms': 0, 'pattern': True}
        elif brain != 'claude' and wfs:
            try:
                model = brain if brain not in ('auto', 'claude') else SMALL
                out, tok, ms = ask_small(route_prompt(part, wfs), model)
                rec['router'] = {'model': model, 'answer': out.get('workflow'), 'tokens': tok, 'ms': ms}
                if out.get('workflow') in ids:   # the simple rule: a real id or nothing
                    picked = ids[out['workflow']]
            except Exception as e:
                rec['router'] = {'error': 'local model not reachable: %s' % e}
        if picked:
            yield {'ev': 'route', 'i': i, 'route': 'workflow', 'workflow': picked['id'], 'name': picked['name'],
                   'steps': [{'id': s['id'], 'kind': s['kind'], 'title': s.get('title', s['id']), 'tool': s.get('tool', '')} for s in picked['steps']]}
            trace = []
            for st in run_steps(picked, part, dry):
                trace.append(st)
                yield dict(st, ev='step', i=i)
            result = trace[-1]['out'] if trace and trace[-1].get('ok') and len(trace) == len(picked['steps']) else None
            rec.update(route='workflow', workflow=picked['id'], steps=trace, result=result,
                       ok=bool(result) and all(s['ok'] for s in trace))
            if trace and trace[-1].get('ask'):
                rec['ask'] = trace[-1]['ask']
        elif brain in ('auto', 'claude') and dry:
            # a test run never wakes Claude: it can act on its own and it costs money
            yield {'ev': 'route', 'i': i, 'route': 'claude'}
            ans, ms = {'ok': True, 'answer': 'Test run: this would go to Claude.'}, 0
            rec.update(route='claude', result=ans, ms=0, ok=True)
        elif brain in ('auto', 'claude'):
            yield {'ev': 'route', 'i': i, 'route': 'claude'}
            ans, ms = ask_claude(part)
            rec.update(route='claude', result=ans, ms=ms, ok=ans['ok'])
        else:
            yield {'ev': 'route', 'i': i, 'route': 'none'}
            rec.update(route='none', ok=False, result={'say': 'No workflow fits and the brain is forced to %s.' % brain})
        log_run(rec)
        yield {'ev': 'done_part', 'i': i, 'ok': rec['ok'], 'route': rec['route'], 'workflow': rec.get('workflow'),
               'result': rec.get('result'), 'router': rec.get('router'), 'ask': rec.get('ask'), 'text': part}
    yield {'ev': 'done'}


def answer(items):
    """Your answers to a flow's questions, each saved in the table that question belongs to."""
    load_code_steps()
    import steps.calendar as cal
    for a in items or []:
        v = str(a.get('value') or a.get('minutes') or '').strip()
        if a.get('type') == 'travel' and v.isdigit():
            cal.save_route(a['route'], int(v))
        elif a.get('type') == 'duration' and v.isdigit():
            cal.save_duration(a['activity'], int(v))
        elif a.get('type') == 'clash' and a.get('save'):
            cal.save_booking_type(a['match'], a['save'])
        elif a.get('type') == 'calendar' and a.get('save'):
            cal.save_calendar_keyword(a['match'], a['save'])
    return True


def recent_runs(n=60):
    d = os.path.join(DATA_DIR, 'runs')
    out = []
    try:
        for name in sorted(os.listdir(d), reverse=True):
            with open(os.path.join(d, name), encoding='utf-8') as f:
                out += [json.loads(l) for l in f if l.strip()][::-1]
            if len(out) >= n:
                break
    except OSError:
        pass
    return out[:n]


# ---------- organising: names, folders, naming rules ----------

def meta_path():
    return os.path.join(DATA_DIR, 'papertrail.json')


def load_meta():
    try:
        with open(meta_path(), encoding='utf-8') as f:
            m = json.load(f)
    except Exception:
        m = {}
    m.setdefault('folders', [])
    m.setdefault('naming_rules', '')
    m.setdefault('models', [])
    return m


def save_meta(m):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(meta_path(), 'w', encoding='utf-8') as f:
        json.dump(m, f, indent=2, ensure_ascii=False)


def update_flow(fid, name=None, folder=None, delete=False):
    """Rename a flow or move it into a folder. The id and file name stay, so old runs still match."""
    for n in os.listdir(wf_dir()):
        if not n.endswith('.json'):
            continue
        p = os.path.join(wf_dir(), n)
        with open(p, encoding='utf-8') as f:
            w = json.load(f)
        if w.get('id') != fid:
            continue
        if delete:
            # never really gone: the flow file moves to the Archive folder next to My Data
            arch = os.path.join(os.path.dirname(DATA_DIR), 'Archive')
            os.makedirs(arch, exist_ok=True)
            os.replace(p, os.path.join(arch, n.replace('.json', ' (deleted %s).json' % time.strftime('%d.%m.%y'))))
            return True
        if name is not None and name.strip():
            w['name'] = name.strip()
        if folder is not None:
            if folder:
                w['folder'] = folder
            else:
                w.pop('folder', None)
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(w, f, indent=2, ensure_ascii=False)
        return True
    return False
