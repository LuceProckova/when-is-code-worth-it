"""same labelling as label_steps.py but on an open model through ollama, local or on a rented gpu

python label_steps_local.py --host https://<pod>-11434.proxy.runpod.net --model qwen2.5:72b-instruct-q4_K_M --reps 2
"""
import argparse, json, os, sys, urllib.request
import label_steps as ls   # same skill, steps, schema and comparison

HERE = os.path.dirname(os.path.abspath(__file__))


def ask(host, model, skill, wf):
    # steps go in without the reference labels, same text as the claude run
    steps = [{'id': s['id'], 'step': s['step']} for s in wf['steps']]
    text = 'Workflow: %s\n\nSteps:\n%s' % (wf['about'], json.dumps(steps, indent=1, ensure_ascii=False))
    body = {'model': model, 'stream': True,   # streamed so the proxy never times out
            'format': ls.schema([s['id'] for s in steps]),
            'options': {'temperature': 0, 'seed': 1, 'num_ctx': 8192},
            'messages': [{'role': 'system', 'content': skill}, {'role': 'user', 'content': text}]}
    req = urllib.request.Request(host.rstrip('/') + '/api/chat', json.dumps(body).encode('utf-8'),
                                 {'content-type': 'application/json', 'user-agent': 'label-steps/1.0'})
    text, msg = '', {}
    for line in urllib.request.urlopen(req, timeout=1800):
        if line.strip():
            msg = json.loads(line)
            text += msg.get('message', {}).get('content', '')
    usage = {'input_tokens': msg.get('prompt_eval_count'), 'output_tokens': msg.get('eval_count'),
             'seconds': round(msg.get('total_duration', 0) / 1e9, 1)}
    return json.loads(text)['steps'], usage


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', default='http://127.0.0.1:11434')
    ap.add_argument('--model', default='qwen2.5:72b-instruct-q4_K_M')
    ap.add_argument('--reps', type=int, default=2)
    a = ap.parse_args()
    skill = open(os.path.join(HERE, 'SKILL.md'), encoding='utf-8').read()
    wfs = json.load(open(os.path.join(HERE, 'steps.json'), encoding='utf-8'))
    runs = []
    for rep in range(1, a.reps + 1):
        for name, wf in wfs.items():
            got, usage = ask(a.host, a.model, skill, wf)
            runs.append({'rep': rep, 'workflow': name, 'model': a.model, 'usage': usage, 'steps': got})
            print('rep %d %s done' % (rep, name), flush=True)
    out = os.path.join(HERE, 'labels-%s.json' % a.model.replace(':', '-'))
    json.dump(runs, open(out, 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
    ref = {s['id']: s['ref'] for wf in wfs.values() for s in wf['steps']}
    opus = {}
    for r in json.load(open(os.path.join(HERE, 'labels-claude-opus-5-5.json'), encoding='utf-8')):
        if r['rep'] == 1:
            for s in r['steps']:
                opus[s['id']] = s['label']
    by = {}
    for r in runs:
        for s in r['steps']:
            by.setdefault(s['id'], {})[r['rep']] = s['label']
    agree = same = op = 0
    for sid in ref:
        labs = [by.get(sid, {}).get(k) for k in sorted(by.get(sid, {}))]
        agree += bool(labs) and labs[0] == ref[sid]
        same += len(set(labs)) == 1
        op += bool(labs) and labs[0] == opus.get(sid)
        print('%-3s person %-26s opus %-26s got %s' % (sid, ref[sid], opus.get(sid), ' / '.join(map(str, labs))))
    n = len(ref)
    print('agrees with the person %d/%d, with opus %d/%d, same every rep %d/%d' % (agree, n, op, n, same, n))
    print('saved', out)


if __name__ == '__main__':
    main()
