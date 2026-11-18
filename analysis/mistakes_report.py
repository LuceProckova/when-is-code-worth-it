"""every mistake of one run with the step it came from

  python mistakes_report.py ../results/<log>.jsonl ../test-data/answer-key.csv mistakes.html
"""
import collections
import html
import json
import sys

import analyse

# where each field lives in step outputs, first match wins
ALIASES = {'title': ['title', 'what'], 'date': ['date', 'day_words', 'day'], 'start': ['start', 'start_words', 'after_event'],
           'end': ['end', 'minutes'], 'all_day': ['allDay', 'all_day'], 'location': ['location', 'where'],
           'attendees': ['attendees', 'people', 'who'], 'calendar': ['calendar'], 'rrule': ['rrule', 'repeat', 'until_words'],
           'description': ['desc', 'notes']}


def esc(v):
    return html.escape('' if v is None else (json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else str(v)))


def show(v):
    if isinstance(v, list):
        return '; '.join((a.get('email') or a.get('name') or '') if isinstance(a, dict) else str(a) for a in v)
    return v


def trail(steps, field):
    """[(step id, kind, value)] for steps whose first row has the field or an alias"""
    out = []
    for st in steps:
        rows = st.get('out')
        if isinstance(rows, dict) and rows.get('dry_run'):
            continue
        r = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else None
        if r is None:
            continue
        for k in ALIASES[field]:
            if k in r and r[k] not in (None, '', [], {}):
                out.append((st['id'], st.get('kind'), k, r[k]))
                break
    return out


def main():
    log, gold_path, out_path = sys.argv[1:4]
    import csv
    gold = {r['id']: r for r in csv.DictReader(open(gold_path, encoding='utf-8-sig'))}
    recs = analyse.read_log(log)
    by_field, by_step = collections.Counter(), collections.Counter()
    cards = []
    n_event = 0
    for d in sorted(recs, key=lambda d: int(d['id'])):
        g = gold[d['id']]
        s = analyse.score_row(g, d.get('result'))
        n_event += s['event']
        res = d.get('result') or {}
        ev = (res.get('events') or [{}])[0]
        wrong = [f for f in analyse.FIELDS if s.get(f) == 0]
        ask_wrong = not s['ask_correct']
        if not wrong and not ask_wrong:
            continue
        rows = []
        for f in wrong:
            by_field[f] += 1
            got = {'all_day': ev.get('allDay'), 'description': ev.get('desc')}.get(f, ev.get(f))
            tr = trail(d.get('steps') or [], f)
            first = tr[0] if tr else None
            if first:
                by_step['%s (%s)' % (first[0], (first[1] or '').upper())] += 1
            chain = ' → '.join('<span class="k %s">%s</span> %s: %s' % (esc(k), esc(i), esc(a), esc(show(v))) for i, k, a, v in tr) or 'no step gave a value'
            rows.append('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (f.replace('_', ' '), esc(show(got)), esc(g.get(f)), chain))
        if ask_wrong:
            kind = 'asked, should not' if s['asked'] else 'did not ask, should'
            by_field['ask: ' + kind] += 1
            rows.append('<tr><td>ask</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
                esc('; '.join(res.get('asks') or []) or 'nothing'), esc(g.get('ask') or ', '.join(f for f in analyse.FIELDS if g.get(f) == 'ask') or 'no ask'), kind))
        cards.append('<section><h3>%s <span class="t">today %s</span></h3><p class="req">%s</p>%s<table><tr><th>Field</th><th>Answer</th><th>Key</th><th>Where it came from</th></tr>%s</table></section>' % (
            esc(d['id']), esc(d.get('today')), esc(d.get('request')),
            '<p class="note">Key note: %s</p>' % esc(g['notes_for_luce']) if g.get('notes_for_luce') else '', ''.join(rows)))
    summ = '<table><tr><th>Field</th><th>Mistakes</th></tr>%s</table>' % ''.join('<tr><td>%s</td><td>%d</td></tr>' % (esc(k), v) for k, v in by_field.most_common())
    summ2 = '<table><tr><th>Step that first gave the wrong value</th><th>Mistakes</th></tr>%s</table>' % ''.join('<tr><td>%s</td><td>%d</td></tr>' % (esc(k), v) for k, v in by_step.most_common())
    page = '''<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>Mistakes</title><style>
body { font: 14px/1.45 "Segoe UI", system-ui, sans-serif; max-width: 1200px; margin: 20px auto; padding: 0 16px; color: #1d1b16; background: #f7f5f0; }
section { background: #fffefb; border: 1px solid #d9d3c4; border-radius: 8px; padding: 8px 12px; margin: 10px 0; }
table { border-collapse: collapse; width: 100%%; table-layout: auto; } th, td { border-bottom: 1px solid #d9d3c4; padding: 3px 6px; text-align: left; vertical-align: top; overflow-wrap: anywhere; }
th { color: #5a554b; font-size: 12px; } h3 { margin: 0 0 4px; } .t { font-weight: 400; color: #5a554b; font-size: 13px; }
.req { background: #e3f1e6; border-radius: 6px; padding: 5px 8px; margin: 4px 0; } .note { color: #8a5a00; font-size: 12px; margin: 2px 0 6px; }
.k { display: inline-block; font-size: 11px; font-weight: 700; color: #fff; border-radius: 4px; padding: 0 5px; }
.k.model { background: #b8860b; } .k.code { background: #7a5c2e; } .k.tool { background: #b5541c; } .k.human { background: #8a7a00; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
</style></head><body><h1>Mistakes, %s</h1><p><b>Key:</b> %s. <b>Whole event right:</b> %d of %d. <b>Requests with a mistake:</b> %d.</p>
<div class="grid"><section>%s</section><section>%s</section></div>%s</body></html>''' % (
        esc(log.replace('\\', '/').split('/')[-1]), esc(gold_path.replace('\\', '/').split('/')[-1]), n_event, len(recs), len(cards), summ, summ2, ''.join(cards))
    open(out_path, 'w', encoding='utf-8').write(page)
    print('written', out_path, '| mistakes in', len(cards), 'requests |', dict(by_field.most_common(6)))


if __name__ == '__main__':
    main()
