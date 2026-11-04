"""Replay: rerun only the code steps of the code flow on the model answers saved in an earlier log (1.11.26).

  python replay.py dev-rounds/r5-1614-partial.jsonl [--out replay.jsonl] [--ids 4,81]

The model steps (intake, calendar and people, notes) are not called again: their saved outputs are fed into the
current code steps. So a code fix can be checked in seconds, without the GPU. Model slips stay the same as in the
saved run, which is the point: only what the code does changes. Not a thesis result, a development tool.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import analyse    # noqa: E402
import dev_log    # noqa: E402
import provenance as pv   # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('log')
    ap.add_argument('--out', default=os.path.join(HERE, 'live', 'replay.jsonl'))
    ap.add_argument('--ids', default='')
    a = ap.parse_args()
    a.log, a.out = os.path.abspath(a.log), os.path.abspath(a.out)
    sys.path.insert(0, pv.LIVE_APP)
    os.chdir(pv.LIVE_APP)
    import papertrail
    papertrail.DATA_DIR = pv.data_dir(pv.LIVE_APP)
    papertrail.load_code_steps()
    wf = next(w for w in papertrail.load_workflows() if w['id'] == 'thesis-create-events')
    want = {x.strip() for x in a.ids.split(',') if x.strip()}
    recs = [d for d in analyse.read_log(a.log) if not want or str(d['id']) in want]
    with open(a.out, 'w', encoding='utf-8') as f:
        f.write(json.dumps({'type': 'header', 'replay_of': a.log}) + '\n')
        for d in recs:
            saved = {s['id']: s for s in d.get('steps') or []}
            ctx = {'input': d['request'], 'test_today': d['today'], 'asks': [], 'dry': True}
            steps = []
            for st in wf['steps']:
                if st['kind'] == 'model':
                    old = saved.get(st['id'], {})
                    val = old.get('out') if old.get('ok') and not old.get('skipped') else (None if st.get('may_be_empty') or old.get('skipped') else [])
                    if isinstance(val, str):
                        val = None
                    for r in (val if isinstance(val, list) else []):   # model questions go to the Ask step, as in a real run
                        q = str(r.get('ask') or '').strip() if isinstance(r, dict) else ''
                        if q and '?' in q and q not in ctx['asks']:   # same as papertrail.run_steps (4.11.26)
                            ctx['asks'].append(q)
                    ctx[st['out']] = val
                    steps.append({'id': st['id'], 'kind': 'model', 'ok': True, 'out': val, 'replayed': True})
                elif st['kind'] == 'code':
                    ctx.pop('_note', None)
                    val = papertrail.CODE_STEPS[st['fn']](papertrail.join_rows(ctx.get(st.get('in')), [ctx.get(j) for j in st.get('join') or []]), ctx)
                    ctx[st['out']] = val
                    papertrail.check_needs(st, val, ctx)
                    steps.append({'id': st['id'], 'kind': 'code', 'ok': True, 'out': val})
                elif st['kind'] == 'human':
                    steps.append({'id': 'ask', 'kind': 'human', 'ok': True, 'out': list(ctx.get('asks') or []),
                                  'ask': [{'type': 'add', 'text': q} for q in ctx.get('asks') or []]})
            rec = dict(type='request', flow='thesis-create-events', id=d['id'], rep=1, today=d['today'], request=d['request'], steps=steps)
            rec['result'] = dev_log.result_of(dict(rec, result=None))
            f.write(json.dumps(rec, ensure_ascii=False, default=list) + '\n')
        f.write(json.dumps({'type': 'block', 'flow': 'thesis-create-events', 'rep': 1}) + '\n')
    print('replayed', len(recs), '->', a.out)


if __name__ == '__main__':
    main()
