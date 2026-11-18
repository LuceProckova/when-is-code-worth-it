"""designer labels every step: person, code, split or model, with the criterion

python label_steps.py --reps 2              (claude-opus-5-5, the designer)
python label_steps.py --model claude-sonnet-5-5 --reps 2
"""
import argparse, json, os, sys, time, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
API = 'https://api.anthropic.com/v1/messages'
LABELS = ['person', 'code', 'code_first_model_fallback', 'model', 'runner']


def key():
    k = os.environ.get('ANTHROPIC_API_KEY')
    kf = os.path.join(os.path.expanduser('~'), '.anthropic-key.txt')   # same key file as run_baseline
    if not k and os.path.exists(kf):
        k = open(kf, encoding='utf-8-sig').read().strip()
    if not k:
        sys.exit('no api key')
    return k


def schema(ids):
    # one answer per step, labels fixed
    item = {'type': 'object', 'additionalProperties': False,
            'required': ['id', 'label', 'criterion', 'why'],
            'properties': {'id': {'type': 'string', 'enum': ids},
                           'label': {'type': 'string', 'enum': LABELS},
                           'criterion': {'type': 'string'},
                           'why': {'type': 'string'}}}
    return {'type': 'object', 'additionalProperties': False, 'required': ['steps'],
            'properties': {'steps': {'type': 'array', 'items': item}}}


def ask(model, skill, wf):
    # steps go in without the reference labels
    steps = [{'id': s['id'], 'step': s['step']} for s in wf['steps']]
    text = 'Workflow: %s\n\nSteps:\n%s' % (wf['about'], json.dumps(steps, indent=1, ensure_ascii=False))
    body = {'model': model, 'max_tokens': 16000, 'system': skill,
            'output_config': {'effort': 'high',
                              'format': {'type': 'json_schema', 'schema': schema([s['id'] for s in steps])}},
            'messages': [{'role': 'user', 'content': text}]}
    req = urllib.request.Request(API, json.dumps(body).encode('utf-8'),
                                 {'x-api-key': key(), 'anthropic-version': '2023-06-01',
                                  'content-type': 'application/json'})
    for attempt in range(5):
        try:
            msg = json.load(urllib.request.urlopen(req, timeout=600))
            break
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 529) and attempt < 4:
                time.sleep(2 ** attempt * 5)
                continue
            sys.exit('HTTP %s %s' % (e.code, e.read().decode('utf-8', 'replace')[:400]))
    if msg.get('stop_reason') == 'refusal':
        sys.exit('refused')
    out = ''.join(b.get('text', '') for b in msg['content'] if b.get('type') == 'text')
    return json.loads(out)['steps'], msg.get('usage', {})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', default='claude-opus-5-5')
    ap.add_argument('--reps', type=int, default=2)
    a = ap.parse_args()
    skill = open(os.path.join(HERE, 'SKILL.md'), encoding='utf-8').read()
    wfs = json.load(open(os.path.join(HERE, 'steps.json'), encoding='utf-8'))
    runs = []
    for rep in range(1, a.reps + 1):
        for name, wf in wfs.items():
            got, usage = ask(a.model, skill, wf)
            runs.append({'rep': rep, 'workflow': name, 'model': a.model, 'usage': usage, 'steps': got})
            print('rep %d %s done' % (rep, name), flush=True)
    out = os.path.join(HERE, 'labels-%s.json' % a.model)
    json.dump(runs, open(out, 'w', encoding='utf-8'), indent=1, ensure_ascii=False)

    # compare with the person's labels and between reps
    ref = {s['id']: s['ref'] for wf in wfs.values() for s in wf['steps']}
    by = {}
    for r in runs:
        for s in r['steps']:
            by.setdefault(s['id'], {})[r['rep']] = s
    agree = same = 0
    for sid in ref:
        labs = [by[sid][rep]['label'] for rep in sorted(by.get(sid, {}))]
        if labs and labs[0] == ref[sid]:
            agree += 1
        if len(set(labs)) == 1:
            same += 1
        print('%-3s ref %-26s got %s  %s' % (sid, ref[sid], ' / '.join(labs),
                                              ' / '.join(by[sid][rep]['criterion'] for rep in sorted(by[sid]))))
    print('rep 1 agrees with the person on %d of %d, same label in every rep on %d of %d' % (agree, len(ref), same, len(ref)))
    print('saved', out)


if __name__ == '__main__':
    main()
