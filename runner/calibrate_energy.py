"""Checks the Zeus energy counter against integrated power polling before any real run.

  python calibrate_energy.py [--idle 30] [--load 30] [--app ...]

Two phases: idle with the model resident, then load (one request looped through the model). In each phase the
counter is read at start and end, Zeus power is polled every 50 ms and integrated, and nvidia-smi power.draw is
polled every 100 ms in parallel. Writes calibration.json with the joules per instrument and the verdict:
counter primary if counter and Zeus poll agree within 5 %, otherwise the poll is primary.
"""
import argparse
import json
import os
import sys
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import provenance as pv   # noqa: E402
from run_tests import Gpu, pin_model   # noqa: E402

PROMPT = 'Write three sentences about planning a study week.'


class SmiPoller:
    def __init__(self, dt=0.1):
        self.dt, self.samples, self._stop = dt, [], threading.Event()

    def _run(self):
        while not self._stop.is_set():
            s = pv.smi(['power.draw'])
            try:
                self.samples.append((time.perf_counter(), float(s['power.draw'])))
            except Exception:
                pass
            time.sleep(self.dt)

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self._stop.set()

    def joules(self, t0, t1):
        s = [x for x in list(self.samples) if t0 <= x[0] <= t1]
        return (sum((b[0] - a[0]) * (a[1] + b[1]) / 2 for a, b in zip(s, s[1:])), len(s)) if len(s) > 1 else (None, len(s))


def load_loop(stop):
    body = json.dumps({'model': pv.MODEL, 'prompt': PROMPT, 'stream': False, 'keep_alive': -1}).encode()
    while not stop.is_set():
        try:
            urllib.request.urlopen(urllib.request.Request(pv.OLLAMA + '/api/generate', data=body,
                                                          headers={'Content-Type': 'application/json'}), timeout=300).read()
        except Exception:
            time.sleep(0.5)


def phase(name, seconds, gpu, smi, load):
    stop = threading.Event()
    if load:
        th = threading.Thread(target=load_loop, args=(stop,), daemon=True)
        th.start()
        time.sleep(2)   # let the first request start
    st0, e0, t0 = pv.gpu_state(), gpu.counter(), time.perf_counter()
    time.sleep(seconds)
    t1, e1, st1 = time.perf_counter(), gpu.counter(), pv.gpu_state()
    stop.set()
    pj, pn = gpu.poll_joules(t0, t1)
    sj, sn = smi.joules(t0, t1)
    c = (e1 - e0) if e0 is not None and e1 is not None else None
    dt = t1 - t0
    diff = abs(c - pj) / pj * 100 if c is not None and pj else None
    r = {'phase': name, 'seconds': round(dt, 3), 'counter_j': c, 'zeus_poll_j': pj, 'zeus_poll_n': pn, 'smi_poll_j': sj,
         'smi_poll_n': sn, 'counter_w': c / dt if c is not None else None, 'zeus_poll_w': pj / dt if pj else None,
         'smi_poll_w': sj / dt if sj else None, 'counter_vs_poll_pct': diff, 'gpu_start': st0, 'gpu_end': st1}
    print(name, json.dumps({k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items() if not k.startswith('gpu_')}))
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--idle', type=float, default=30)
    ap.add_argument('--load', type=float, default=30)
    ap.add_argument('--tolerance', type=float, default=5.0)
    ap.add_argument('--out', default=os.path.join(HERE, 'calibration.json'))
    a = ap.parse_args()
    gpu, smi = Gpu(), SmiPoller()
    if not gpu.g:
        sys.exit('no Zeus GPU: nothing to calibrate')
    gpu.start()
    smi.start()
    try:
        pinned = pin_model()
        time.sleep(3)
        phases = [phase('idle', a.idle, gpu, smi, False), phase('load', a.load, gpu, smi, True)]
    finally:
        gpu.stop()
        smi.stop()
    agree = all(p['counter_vs_poll_pct'] is not None and p['counter_vs_poll_pct'] <= a.tolerance for p in phases)
    res = {'time': time.strftime('%Y-%m-%dT%H:%M:%S'), 'tolerance_pct': a.tolerance, 'power_call': gpu.power_call,
           'pinned': pinned, 'primary': 'counter' if agree else 'zeus_poll', 'agree': agree, 'phases': phases,
           'versions': pv.versions()}
    with open(a.out, 'w', encoding='utf-8') as f:
        json.dump(res, f, indent=1)
    print('primary:', res['primary'], '->', a.out)


if __name__ == '__main__':
    main()
