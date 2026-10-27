import csv, html, sys, datetime as dt
src, out = sys.argv[1], sys.argv[2]
rows = list(csv.DictReader(open(src, encoding='utf-8-sig')))
def today(s):
    d = dt.datetime.strptime(s, '%Y-%m-%d %H:%M')
    return d.strftime('%a %d %b, %H:%M').replace(' 0', ' ')
tr = []
for r in rows:
    tags = []
    if r['ask']: tags.append('<span class="t ask">❓ should ask</span>')
    if 'slip known' in r['sources']: tags.append('<span class="t">slip, in table</span>')
    if 'slip unseen' in r['sources']: tags.append('<span class="t">slip, not in table</span>')
    tr.append(f'<tr{" class=\"askrow\"" if r["ask"] else ""}><td class="n">{r["id"]}</td><td class="d">{today(r["today"])}</td>'
              f'<td><div class="req">{html.escape(r["request"])}</div><div class="meta">{" ".join(tags)} <span class="codes">{html.escape(r["codes"])}</span></div></td></tr>')
page = f'''<!doctype html>
<html lang="en-GB"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Test Requests</title>
<style>
:root{{--bg:#fbfaf7;--fg:#1f1f1f;--muted:#777;--line:#e2e2e2;--accent:#2f5d8a;--ask:#fff4dc;--chip:#eef4fa}}
@media (prefers-color-scheme: dark){{:root:not([data-theme="light"]){{--bg:#1a1a1a;--fg:#e8e8e8;--muted:#999;--line:#333;--accent:#8db8e3;--ask:#3a2f17;--chip:#1f2a36}}}}
:root[data-theme="dark"]{{--bg:#1a1a1a;--fg:#e8e8e8;--muted:#999;--line:#333;--accent:#8db8e3;--ask:#3a2f17;--chip:#1f2a36}}
body{{background:var(--bg);color:var(--fg);font:16px/1.55 Arial,sans-serif;max-width:900px;margin:0 auto;padding:24px 16px}}
h1{{font-size:22px;margin:0 0 4px}}.sub{{color:var(--muted);margin:0 0 18px}}
table{{border-collapse:collapse;width:100%}}td{{border-bottom:1px solid var(--line);padding:10px 8px;vertical-align:top}}
td.n{{color:var(--accent);font-weight:bold;width:2.5em}}td.d{{color:var(--muted);font-size:14px;white-space:nowrap;width:9em}}
.req{{font-size:17px}}.meta{{margin-top:4px;font-size:12px;color:var(--muted)}}
.t{{display:inline-block;background:var(--chip);border-radius:4px;padding:1px 6px;margin-right:4px}}
.codes{{font-family:Consolas,monospace}}tr.askrow{{background:var(--ask)}}
@media (max-width:600px){{td.d{{white-space:normal;width:auto}}}}
</style></head><body>
<h1>Test requests</h1>
<p class="sub">{len(rows)} requests. The date is the "today" each one is asked on.</p>
<table>{"".join(tr)}</table>
</body></html>'''
open(out, 'w', encoding='utf-8').write(page)
