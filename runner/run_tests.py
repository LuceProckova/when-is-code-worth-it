"""runs test requests through flows, logs every run

  python run_tests.py --flow thesis-create-events --ids 1,7,23 --reps 3          (dry by default)
  python run_tests.py --live ...                                                 (refused unless test_calendar_id is set)
"""
import argparse
import csv
import json
import os
import random
import sys
import threading
import time
import urllib.request
import warnings

warnings.filterwarnings('ignore')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import provenance as pv   # noqa: E402

papertrail = None


class Gpu:
    """zeus counter plus 50 ms power poller on gpu 0, none without gpu"""

    def __init__(self, dt=0.05):
        self.g, self.dt, self.samples, self.power_call = None, dt, [], None
        self._stop = threading.Event()
        try:
            from zeus.device import get_gpus
            self.g = get_gpus()
        except Exception:
            return
        for name in ('get_instant_power_usage', 'get_average_power_usage'):
            try:
                getattr(self.g, name)(0)
                self.power_call = name
                break
            except Exception:
                pass

    def counter(self):
        try:
            return self.g.get_total_energy_consumption(0) / 1000 if self.g else None
        except Exception:
            return None

    def _run(self):
        f = getattr(self.g, self.power_call)
        while not self._stop.is_set():
            try:
                self.samples.append((time.perf_counter(), f(0) / 1000))
            except Exception:
                pass
            time.sleep(self.dt)

    def start(self):
        if self.g and self.power_call:
            self.t = threading.Thread(target=self._run, daemon=True)
            self.t.start()

    def stop(self):
        self._stop.set()

    def poll_joules(self, t0, t1):
        s = [(t, p) for t, p in list(self.samples) if t0 <= t <= t1]
        if len(s) < 2:
            return None, len(s)
        return sum((b[0] - a[0]) * (a[1] + b[1]) / 2 for a, b in zip(s, s[1:])), len(s)


def window(gpu, fn, min_s):
    """run fn, hold window open min_s, return (result, measurement)"""
    st0, e0, t0 = pv.gpu_state(), gpu.counter(), time.perf_counter()
    res = fn()
    run_s = time.perf_counter() - t0
    if run_s < min_s:
        time.sleep(min_s - run_s)
    t1, e1, st1 = time.perf_counter(), gpu.counter(), pv.gpu_state()
    pj, n = gpu.poll_joules(t0, t1)
    return res, {'t_start': round(t0, 4), 't_end': round(t1, 4), 'seconds': round(run_s, 3),
                 'window_s': round(t1 - t0, 3), 'short_window': run_s < min_s,
                 'gpu_joules_counter': round(e1 - e0, 3) if e0 is not None and e1 is not None else None,
                 'gpu_joules_poll': round(pj, 3) if pj is not None else None, 'n_samples': n,
                 'gpu_start': st0, 'gpu_end': st1}


def pin_model():
    """keep model loaded, papertrail sends no keep_alive"""
    try:
        req = urllib.request.Request(pv.OLLAMA + '/api/generate', data=json.dumps({'model': pv.MODEL, 'keep_alive': -1, 'options': {'num_ctx': 8192}}).encode(),
                                     headers={'Content-Type': 'application/json'})
        urllib.request.urlopen(req, timeout=120).read()
        return True
    except Exception as e:
        return str(e)


def final_result(steps):
    """events from check plus ask questions, same shape as one-prompt, none if stopped before check"""
    chk = next((s for s in steps if s['id'] == 'check' and s.get('ok')), None)
    if chk is None:
        return None
    ask = next((s for s in steps if s['id'] == 'ask'), {})
    asks = [q.get('text') if isinstance(q, dict) else str(q) for q in ask.get('ask') or []]
    events = chk['out'] if isinstance(chk.get('out'), list) else []
    return {'events': events, 'asks': asks}


def settings_value(app, flow, key):
    data = pv.data_dir(app)
    for t in pv.flow_tables(data, flow):
        if os.path.basename(t) == 'settings.csv':
            with open(t, encoding='utf-8-sig') as f:
                for row in csv.DictReader(f):
                    if row.get('setting') == key and (row.get('value') or '').strip():
                        return row['value'].strip()
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--app', default=pv.LIVE_APP)
    ap.add_argument('--model', default=pv.MODEL, help='Ollama model for every model step, e.g. qwen2.5:72b-instruct-q4_K_M (4.11.26)')
    ap.add_argument('--flow', default='thesis-create-events', help='flow id, or a comma list')
    ap.add_argument('--ids', default='')
    ap.add_argument('--reps', type=int, default=1)
    ap.add_argument('--seed', type=int, default=2610)
    ap.add_argument('--warmup', type=int, default=5)
    ap.add_argument('--idle-seconds', type=float, default=30)
    ap.add_argument('--min-window', type=float, default=2.0)
    ap.add_argument('--live', action='store_true', help='really write events (refused unless test_calendar_id is set)')
    ap.add_argument('--note', default='')
    ap.add_argument('--out', default='', help='log file; appended to if it exists (the dashboard uses this to resume)')
    ap.add_argument('--start-rep', type=int, default=1, help='first repeat to run, after a stop (earlier repeats are already in --out)')
    a = ap.parse_args()
    flows = [f.strip() for f in a.flow.split(',') if f.strip()]
    dry = not a.live

    if a.live:
        for fl in flows:
            if not settings_value(a.app, fl, 'test_calendar_id'):
                sys.exit('REFUSED: --live needs test_calendar_id in the settings table of flow %s' % fl)

    global papertrail
    sys.path.insert(0, a.app)
    os.chdir(a.app)
    import papertrail as _pt
    papertrail = _pt
    papertrail.DATA_DIR = pv.data_dir(a.app)
    pv.MODEL = papertrail.SMALL = a.model
    papertrail.ask_small.__defaults__ = (a.model, None)
    papertrail.OLLAMA_OPTIONS = {'temperature': 0, 'seed': 1, 'num_ctx': 8192, 'num_predict': 1024}   # same as run_baseline, runaway answer stops at 1024 tokens
    papertrail.short = lambda v, limit=None: v   # log keeps full step output, not app's short view

    with open(pv.REQUESTS, encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))
    if a.ids:
        want = {x.strip() for x in a.ids.split(',')}
        rows = [r for r in rows if r['id'] in want]
    wfs = {w['id']: w for w in papertrail.load_workflows() if w['id'] in flows}
    missing = [f for f in flows if f not in wfs]
    if missing:
        sys.exit('unknown flow(s): %s' % missing)

    runner_files = [os.path.join(HERE, n) for n in ('run_tests.py', 'provenance.py')]
    hashes = pv.file_hashes(a.app, flows, extra=runner_files)
    out = a.out or os.path.join(HERE, '%s-%s.jsonl' % ('+'.join(flows), time.strftime('%Y%m%d-%H%M%S')))
    gpu = Gpu()

    def log(rec):
        with open(out, 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')

    log({'type': 'header', 'time': time.strftime('%Y-%m-%dT%H:%M:%S'), 'args': vars(a), 'dry': dry, 'flows': flows,
         'n_requests': len(rows), 'versions': pv.versions(), 'power_call': gpu.power_call,
         'combined': {p: pv.combined(hashes, p) for p in ('requests', 'flow/', 'tables/', 'prompt/', 'code/', 'runner/')},
         'hashes': hashes})
    print('log:', out)

    def run_one(wf, r, rep, warm):
        extra = {'test_today': r['today'], 'asks': []}
        if not warm:
            extra['test_id'] = r['id']
        return list(papertrail.run_steps(wf, r['request'], dry=dry, extra=extra))

    gpu.start()
    try:
        for rep in range(a.start_rep, a.reps + 1):
            now = pv.file_hashes(a.app, flows, extra=runner_files)
            if now != hashes:
                changed = sorted(k for k in set(now) | set(hashes) if now.get(k) != hashes.get(k))
                log({'type': 'abort', 'rep': rep, 'changed': changed})
                sys.exit('ABORT: provenance changed before repeat %d: %s' % (rep, changed))
            for ci, fl in enumerate(flows):
                wf = wfs[fl]
                pinned = pin_model()
                # warmup hits model even in dry mode, discarded
                rng_w = random.Random(a.seed * 7 + rep)
                for wi in range(a.warmup):
                    r = rng_w.choice(rows)
                    _, m = window(gpu, lambda: run_one(wf, r, rep, True), 0)
                    log(dict(type='warmup', warmup=True, rep=rep, flow=fl, id=r['id'], **m))
                    print('warm-up', wi + 1, r['id'], '%.1fs' % m['seconds'])
                _, idle = window(gpu, lambda: time.sleep(a.idle_seconds), 0)
                src = idle['gpu_joules_counter'] if idle['gpu_joules_counter'] is not None else idle['gpu_joules_poll']
                idle_w = round(src / idle['window_s'], 3) if src is not None else None
                log(dict(type='idle', rep=rep, flow=fl, pinned=pinned, idle_w=idle_w,
                         idle_w_poll=round(idle['gpu_joules_poll'] / idle['window_s'], 3) if idle['gpu_joules_poll'] else None, **idle))
                print('idle', a.idle_seconds, 's ->', idle_w, 'W')
                order = rows[:]
                random.Random(a.seed * 1000 + rep * 10 + ci).shuffle(order)
                b0, bt0, bp0 = gpu.counter(), time.perf_counter(), pv.gpu_state()
                n_ok = 0
                for pos, r in enumerate(order, 1):
                    print('@@now', fl, rep, pos, r['id'], flush=True)
                    steps, m = window(gpu, lambda: run_one(wf, r, rep, False), a.min_window)
                    result = final_result(steps)
                    err = next((s['out'] for s in steps if not s.get('ok')), None)
                    gross = m['gpu_joules_counter']
                    rec = dict(type='request', time=time.strftime('%Y-%m-%dT%H:%M:%S'), flow=fl, id=r['id'], rep=rep,
                               order_in_rep=ci + 1, pos_in_block=pos, seed=a.seed, warmup=False, dry=dry, today=r['today'],
                               request=r['request'], result=result, ok=all(s.get('ok') for s in steps), error=err,
                               tokens=sum(s.get('tokens') or 0 for s in steps), idle_w=idle_w,
                               gpu_joules_net=round(gross - idle_w * m['window_s'], 3) if gross is not None and idle_w is not None else None,
                               **m, steps=steps)
                    n_ok += rec['ok']
                    log(rec)
                    print(rep, pos, r['id'], '%.1fs' % m['seconds'], gross, 'J', m['gpu_end'].get('temperature.gpu'), 'C',
                          '| ERROR ' + str(err) if err else '', json.dumps(result, ensure_ascii=False)[:200])
                b1, bt1, bp1 = gpu.counter(), time.perf_counter(), pv.gpu_state()
                bpj, _ = gpu.poll_joules(bt0, bt1)
                log({'type': 'block', 'rep': rep, 'flow': fl, 'order_in_rep': ci + 1, 'n': len(order), 'n_ok': n_ok,
                     'block_seconds': round(bt1 - bt0, 3),
                     'block_joules_counter': round(b1 - b0, 3) if b0 is not None and b1 is not None else None,
                     'block_joules_poll': round(bpj, 3) if bpj is not None else None, 'idle_w': idle_w,
                     'gpu_start': bp0, 'gpu_end': bp1})
    finally:
        gpu.stop()
    print('done:', out)


if __name__ == '__main__':
    main()
