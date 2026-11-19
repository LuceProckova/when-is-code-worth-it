"""freezes measured run files into Release\\v1.0 with hash manifest

  python make_release.py [--target <folder>] [--app ...] [--flow thesis-create-events]
"""
import argparse
import glob
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import provenance as pv   # noqa: E402

RUNNER = ['run_tests.py', 'provenance.py', 'make_release.py']


def listed(app, flows):
    """[(source, path in release)]"""
    data = pv.data_dir(app)
    out = [(os.path.join(HERE, n), 'runner/' + n) for n in RUNNER if os.path.exists(os.path.join(HERE, n))]
    out.append((pv.REQUESTS, 'test-data/requests.csv'))
    for fid in flows:
        p = pv.flow_path(data, fid)
        out.append((p, 'versions/%s/%s.json' % (os.path.basename(os.path.dirname(p)), fid)))
        for t in pv.flow_tables(data, fid):
            out.append((t, 'test-data/tables/' + os.path.basename(t)))
    for c in pv.code_files(app):
        out.append((c, 'app/' + os.path.relpath(c, app).replace('\\', '/')))
    seen, uniq = set(), []
    for s, d in out:
        if d not in seen:
            seen.add(d)
            uniq.append((s, d))
    return uniq


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--target', default=os.path.join(HERE, 'Release', 'v1.0'))
    ap.add_argument('--app', default=pv.LIVE_APP)
    ap.add_argument('--flow', default='thesis-create-events')
    a = ap.parse_args()
    flows = [f.strip() for f in a.flow.split(',') if f.strip()]
    if os.path.isdir(a.target) and os.listdir(a.target):
        sys.exit('REFUSED: %s is not empty' % a.target)
    files = listed(a.app, flows)
    for src, rel in files:
        dst = os.path.join(a.target, *rel.split('/'))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
    v = pv.versions()
    with open(os.path.join(a.target, 'ENVIRONMENT.txt'), 'w', encoding='utf-8') as f:
        f.write('flows: %s\napp: %s\n' % (', '.join(flows), a.app))
        for k, val in v.items():
            f.write('%s: %s\n' % (k, json.dumps(val, ensure_ascii=False) if isinstance(val, (dict, list)) else val))
        f.write('gpu_state_at_release: %s\n' % json.dumps(pv.gpu_state()))
    lines = []
    for p in sorted(glob.glob(os.path.join(a.target, '**', '*'), recursive=True)):
        if os.path.isfile(p) and os.path.basename(p) != 'MANIFEST.sha256':
            lines.append('%s  %s' % (pv.sha256(p), os.path.relpath(p, a.target).replace('\\', '/')))
    with open(os.path.join(a.target, 'MANIFEST.sha256'), 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(lines) + '\n')
    print('%d files + ENVIRONMENT.txt, manifest %d lines -> %s' % (len(files), len(lines), a.target))


if __name__ == '__main__':
    main()
