"""Score run logs against gold and write the thesis statistics.

Usage: python analyse.py --gold gold.csv --requests ../10-Test-Data/requests.csv log1.jsonl [log2.jsonl ...]
Writes results/summary.md, results/per_field.csv, results/per_option.csv and 3 PNG plots.
Scoring rules: score_rules.md (same folder).
"""
import argparse, csv, json, re, os, unicodedata, collections
from datetime import datetime
import numpy as np
from scipy.stats import binomtest, wilcoxon, norm
from dateutil.rrule import rrulestr

FIELDS = ["title", "date", "start", "end", "all_day", "location", "attendees", "calendar", "rrule", "description"]
# Option letters as defined in Test-set.html section 1. L (duration) is scored through end, W (today) through date.
FIELD_LETTER = {"W": "date", "D": "date", "S": "start", "L": "end", "T": "title", "P": "location",
                "A": "attendees", "N": "description", "C": "calendar", "R": "rrule"}
ASK_WORDS = {"calendar": ["calendar"], "attendees": ["which", "who", "attendee", "invite"],
             "date": ["day", "date", "which day"], "start": ["start", "time", "morning", "all day", " or ", "clash", "book anyway"],
             "end": ["end", "how long", "length"], "location": ["where", "location"]}
SEED = 20261030


def norm_text(s):
    s = unicodedata.normalize("NFKC", str(s or "")).casefold()
    s = re.sub(r"[^\w\s()>-]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


PLACES_CSV = "C:/Users/lucyp/Desktop/All-My-Stuff/0-AI-Solutions/08-Master-Interface/03 - My Data/knowledge/thesis/places.csv"
PLACE_ADDRESS = None


def place_by_address(t):
    """A place written as its address in the places table counts as that place ("5 Market Street" = "Office", 2.11.26)."""
    global PLACE_ADDRESS
    if PLACE_ADDRESS is None:
        import csv as _csv
        PLACE_ADDRESS = {}
        if os.path.exists(PLACES_CSV):
            for r in _csv.DictReader(open(PLACES_CSV, encoding="utf-8-sig")):
                a = (r.get("address") or "").strip()
                if a and a.lower() != "online":
                    PLACE_ADDRESS[norm_text(a)] = norm_text(r["place"])
    return PLACE_ADDRESS.get(t, t)


def norm_place(s):
    """A place as text: "room B204" = "B204", "Vienna to Bratislava" = "Vienna -> Bratislava"."""
    t = place_by_address(norm_text(s))
    t = re.sub(r"^(in|at|the|room)\s+", "", t)
    t = re.sub(r"^(in|at|the|room)\s+", "", t)
    return re.sub(r"\s*(->|-\s*>|\bto\b)\s*", " -> ", t).strip()


def norm_title(s):
    s = norm_text(s)
    s = re.sub(r"^(set up|schedule|book|put|add)\s+", "", s)
    return re.sub(r"[^\w()]", "", s.replace(" ", ""))


TITLE_STOP = {"the", "a", "an", "that", "this", "my", "our", "have", "do", "get", "go", "with", "and", "of", "to", "for", "at", "on", "in", "some", "thing"}
PREFIX = re.compile(r"^(maybe\s+)?(\((\w+)\)\s*|temp\s*-\s*(\w+)\s*-\s*|ph\s*-\s*(\w+)\s*-\s*)?", re.I)


def title_parts(t):
    """(prefix, people, content stems) of a title: "MAYBE (NW) Long breakfast with Martin and the client"."""
    t = unicodedata.normalize("NFKC", str(t or "")).strip()
    tags = [x.upper() for x in re.findall(r"\((NW|FAB)\)", t, re.I)]   # a company tag counts wherever it stands (4.11.26)
    t = re.sub(r"\s*\((?:NW|FAB)\)\s*", " ", t, flags=re.I).strip()
    m = PREFIX.match(t)
    pre = (re.sub(r"\s+", " ", (m.group(0) if m else "")).strip().casefold() + " " + " ".join("(%s)" % x.lower() for x in tags)).strip()
    rest = t[m.end():] if m else t
    what, _, who = rest.partition(" with ")
    people = frozenset(x.strip().casefold() for x in re.split(r"\s+and\s+|,", who) if x.strip())
    words = {w[:5] for w in re.findall(r"[\w']+", what.casefold()) if w not in TITLE_STOP}
    return pre, people, words


NAMES = {"martin", "sam", "jordan", "natalie", "arthur", "mum", "dad", "dana", "team", "client"}


def title_same(gold, pred):
    """Prefix exact; the people named anywhere in the title the same set ("Team lunch" = "Lunch with the team");
    the other event words free: the two share at least one content word (2.11.26: the key was too harsh for a
    model that words things its own way; was: one set of words contains the other)."""
    g, p = title_parts(gold), title_parts(pred)
    if g[0] != p[0]:
        return False
    def split(t):
        rest = PREFIX.sub("", unicodedata.normalize("NFKC", str(t or "")).strip(), count=1).casefold()
        words = {w for w in re.findall(r"[\w']+", rest) if w not in TITLE_STOP}
        names = {w for w in words if w.rstrip("'s") in NAMES and not w.endswith("'s")}
        if not names:   # "Dinner at Mum's" names Mum, as "Dinner with Mum" does (4.11.26; rule 2.11.26: at Mum's = with Mum)
            names = {w[:-2] for w in words if w.endswith("'s") and w[:-2] in NAMES}
        return names, {w[:5] for w in words - names}
    gn, gw = split(gold)
    pn, pw = split(pred)
    return gn == pn and bool(gw) and bool(pw) and bool(gw & pw)


def emails(v):
    if isinstance(v, list):
        out = [(a.get("email") or a.get("name") or "") if isinstance(a, dict) else str(a) for a in v]
    else:
        out = re.split(r"[;,]", str(v or ""))
    return frozenset(x.strip().casefold() for x in out if x.strip())


def boolish(v):
    if v in (True, "TRUE", "true", "True", 1): return True
    return False  # None/""/FALSE all mean not all-day


def key_items(text):
    """Key information a description must keep: links, numbers, codes."""
    t = str(text or "")
    items = re.findall(r"https?://\S+", t)
    t2 = re.sub(r"https?://\S+", " ", t)
    items += [re.sub(r"\s", "", m) for m in re.findall(r"\+?\d[\d\s]{2,}\d|\d+", t2)]
    items += re.findall(r"\b[A-Z]{2,}\d*\b", t2)
    return set(i.rstrip(".,)") for i in items)


def rrule_same(gold, pred, start):
    day3 = lambda r: re.sub(r"\b(MON|TUE|WED|THU|FRI|SAT|SUN)[A-Z]*\b", lambda m: m.group(1)[:2], r.upper())   # THU = TH
    g, p = day3((gold or "").strip()), day3((pred or "").strip())
    if not g or not p:
        return not g and not p
    try:
        horizon = start.replace(year=start.year + 1)
        def occ(r):
            r = re.sub(r"UNTIL=(\d{8})(?!T)", r"UNTIL=\1T235959", r)
            return [d for d in rrulestr(r, dtstart=start).between(start, horizon, inc=True)]
        return occ(g) == occ(p)
    except Exception:
        return g.upper() == p.upper()


def asked_about(asks, field):
    text = " ".join(asks).casefold()
    return any(w in text for w in ASK_WORDS.get(field, [field]))


def score_row(gold, result):
    """Return dict field -> 0/1/None (None = not scored) plus ask outcome."""
    events = (result or {}).get("events") or []
    asks = [str(a) for a in ((result or {}).get("asks") or [])]
    should_ask_row = bool(gold["ask"].strip())
    clash = bool((gold.get("clash") or "").strip())   # overlaps an existing event: ask "book anyway?", fields still marked (3.11.26)
    should_ask_any = should_ask_row or clash or any(gold[f] == "ask" for f in FIELDS)
    out = {"asked": int(bool(asks)), "should_ask": int(should_ask_any)}
    out["ask_correct"] = int(bool(asks) == should_ask_any)
    if should_ask_row:  # contradiction row: only the ask is scored
        out.update({f: None for f in FIELDS})
        out["event"] = int(bool(asks) and asked_about(asks, "start"))
        return out
    ev = events[0] if events else None   # several events: the fields of the first are still marked one by one (2.11.26)
    try:
        start = datetime.fromisoformat(f"{gold['date']}T{gold['start'] or '00:00'}")
    except ValueError:
        start = None
    for f in FIELDS:
        gv = gold[f]
        if gv == "ask":
            out[f] = int(asked_about(asks, f))
            continue
        if ev is None:
            out[f] = 0
            continue
        pv = {"all_day": ev.get("allDay"), "description": ev.get("desc")}.get(f, ev.get(f))
        if f in ("date", "start", "end", "calendar"):
            out[f] = int((gv or "") == (pv or ""))
        elif f == "all_day":
            out[f] = int(boolish(gv) == boolish(pv))
        elif f == "location":
            out[f] = int(norm_place(gv) == norm_place(pv))
        elif f == "attendees":
            out[f] = int(emails(gv) == emails(pv))
        elif f == "title":
            out[f] = int(norm_title(gv) == norm_title(pv) or title_same(gv, pv))
        elif f == "rrule":
            out[f] = int(rrule_same(gv, pv, start)) if start else int(gv == (pv or ""))
        elif f == "description":
            kept = key_items(gv) <= key_items(pv) | {re.sub(r"\s", "", x) for x in key_items(pv)}
            out[f] = int(kept and not (str(gv or "").strip() and not str(pv or "").strip()))   # a note in the key must not be left out (4.11.26)
    scored = [out[f] for f in FIELDS if out[f] is not None]
    out["extra_events"] = max(len(events) - 1, 0)
    out["event"] = int(all(scored) and (not asks or should_ask_any) and (bool(asks) or not clash) and len(events) == 1)   # an extra event still makes the request wrong
    return out


FLOW_CONDITION = {"thesis-create-events": "code", "thesis-create-events-allmodel": "allmodel"}
# planned paired comparisons (first is primary): only the pairs whose two conditions are in the logs are run
PAIRS = [("allmodel", "code"), ("oneprompt72b", "oneprompt7b"), ("oneprompt7b", "allmodel"), ("oneprompt7b", "code"),
         ("claude", "oneprompt72b")]


def read_log(path, partial=False):
    """Request records from a run_tests.py or run_baseline.py log, each with a condition and one energy value.
    run_tests.py: only repeats that finished with a block line count, so a repeat cut short by a stop is left out."""
    recs, blocks = [], set()
    for line in open(path, encoding="utf-8"):
        if not line.strip():
            continue
        d = json.loads(line)
        t = d.get("type")
        if t == "block":
            blocks.add((d["flow"], d["rep"]))
        if t in ("header", "idle", "block", "warmup", "abort") or d.get("warmup"):
            continue
        d["condition"] = d.get("condition") or FLOW_CONDITION.get(d.get("flow"), d.get("flow"))
        if d.get("gpu_joules") is None:
            d["gpu_joules"] = d.get("gpu_joules_net") if d.get("gpu_joules_net") is not None else d.get("gpu_joules_counter")
        recs.append(d)
    if partial:   # development log only: a run stopped early still shows what it did
        return recs
    return [d for d in recs if "flow" not in d or (d["flow"], d["rep"]) in blocks]


def canon(result):
    return json.dumps(result or {}, sort_keys=True, ensure_ascii=False)


def wilson(k, n, z=1.96):
    if n == 0: return (float("nan"),) * 2
    p = k / n; d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d; h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


def mcnemar(a, b):
    b01 = sum(1 for x, y in zip(a, b) if x == 1 and y == 0)
    b10 = sum(1 for x, y in zip(a, b) if x == 0 and y == 1)
    p = binomtest(b01, b01 + b10, 0.5).pvalue if b01 + b10 else 1.0
    return b01, b10, p


def holm(ps):
    order = np.argsort(ps); adj = [0] * len(ps); m = len(ps); run = 0
    for i, j in enumerate(order):
        run = max(run, min(1, (m - i) * ps[j])); adj[j] = run
    return adj


def boot_ratio(x, y, n=10000):
    rng = np.random.default_rng(SEED); x, y = np.asarray(x), np.asarray(y)
    lr = np.log(y) - np.log(x); idx = rng.integers(0, len(lr), (n, len(lr)))
    b = np.exp(lr[idx].mean(1))
    return float(np.exp(lr.mean())), float(np.percentile(b, 2.5)), float(np.percentile(b, 97.5))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("logs", nargs="+")
    ap.add_argument("--gold", required=True)
    ap.add_argument("--requests", required=True)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "results"))
    ap.add_argument("--include-dry", action="store_true", default=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    gold = {r["id"]: r for r in csv.DictReader(open(a.gold, encoding="utf-8-sig"))}
    codes = {r["id"]: r["codes"].split() for r in csv.DictReader(open(a.requests, encoding="utf-8-sig"))}

    runs = collections.defaultdict(lambda: collections.defaultdict(list))
    for path in a.logs:
        for d in read_log(path):
            runs[d["condition"]][str(d["id"])].append(d)
    conds = sorted(runs)
    ids = sorted(gold, key=int)

    scores, cost, stability = {}, {}, {}
    for c in conds:
        scores[c], cost[c], stability[c] = {}, {}, {}
        for i in ids:
            rs = runs[c].get(i, [])
            if not rs:
                continue
            cnt = collections.Counter(canon(r.get("result")) for r in rs)
            modal, k = cnt.most_common(1)[0]
            stability[c][i] = k / len(rs)
            scores[c][i] = score_row(gold[i], json.loads(modal))
            def m(key):
                v = [r.get(key) for r in rs if r.get(key) is not None]
                return float(np.mean(v)) if v else float("nan")
            cost[c][i] = {"joules": m("gpu_joules"), "seconds": m("seconds"), "tokens": m("tokens")}

    metrics = FIELDS + ["event", "ask_correct"]
    rows = []
    for c in conds:
        for f in metrics:
            v = [s[f] for s in scores[c].values() if s[f] is not None]
            lo, hi = wilson(sum(v), len(v))
            rows.append({"condition": c, "field": f, "n": len(v), "correct": sum(v),
                         "accuracy": round(sum(v) / len(v), 4) if v else "", "ci_low": round(lo, 4), "ci_high": round(hi, 4)})
    with open(os.path.join(a.out, "per_field.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)

    orow = []
    for c in conds:
        bucket = collections.defaultdict(list)
        for i, s in scores[c].items():
            for code in codes.get(i, []):
                letter = code[0]
                f = FIELD_LETTER.get(letter)
                if letter == "R" and code != "R1": code = "R2-R10"
                if f and s.get(f) is not None:
                    bucket[(code, f)].append(s[f])
        for (code, f), v in sorted(bucket.items()):
            lo, hi = wilson(sum(v), len(v))
            orow.append({"condition": c, "option": code, "field": f, "n": len(v), "accuracy": round(sum(v) / len(v), 4),
                         "ci_low": round(lo, 4), "ci_high": round(hi, 4)})
    with open(os.path.join(a.out, "per_option.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["condition", "option", "field", "n", "accuracy", "ci_low", "ci_high"])
        w.writeheader(); w.writerows(orow)

    L = ["# Results summary", "", f"Logs: {', '.join(os.path.basename(p) for p in a.logs)}. Gold: {os.path.basename(a.gold)}. Unit: request, modal output across repeats.", ""]
    L += ["## Accuracy per field (Wilson 95% CI)", "", "| condition | field | n | correct | accuracy | 95% CI |", "|---|---|---|---|---|---|"]
    L += [f"| {r['condition']} | {r['field']} | {r['n']} | {r['correct']} | {r['accuracy']} | {r['ci_low']} to {r['ci_high']} |" for r in rows]
    L += ["", "## Fields right per request (mean of the scored fields, out of 10; contradiction rows left out)", ""]
    for c in conds:
        per = [sum(x[f] for f in FIELDS if x[f] is not None) for x in scores[c].values() if any(x[f] is not None for f in FIELDS)]
        if per:
            L.append(f"- {c}: mean {np.mean(per):.2f} of 10, median {np.median(per):.1f}, n {len(per)}")
    L += ["", "## Asks", "", "| condition | true ask | false ask | missed ask | recall | false-ask rate |", "|---|---|---|---|---|---|"]
    for c in conds:
        s = scores[c].values()
        ta = sum(1 for x in s if x["asked"] and x["should_ask"]); fa = sum(1 for x in s if x["asked"] and not x["should_ask"])
        ma = sum(1 for x in s if not x["asked"] and x["should_ask"]); pos = ta + ma; neg = len(scores[c]) - pos
        L.append(f"| {c} | {ta} | {fa} | {ma} | {ta}/{pos} | {fa}/{neg} |")
    L += ["", "## Stability (share of repeats equal to the modal output)", ""]
    for c in conds:
        v = list(stability[c].values())
        L.append(f"- {c}: mean {np.mean(v):.3f}, requests with any disagreement {sum(1 for x in v if x < 1)}, repeats per request median {int(np.median([len(runs[c][i]) for i in runs[c]]))}")
    L += ["", "## Energy and time per request (mean of repeats)", ""]
    for c in conds:
        for k in ("joules", "seconds", "tokens"):
            v = np.array([cost[c][i][k] for i in cost[c]]); v = v[~np.isnan(v)]
            if len(v):
                L.append(f"- {c} {k}: median {np.median(v):.2f}, mean {v.mean():.2f}, n {len(v)}")

    pairs = [(x, y) for x, y in PAIRS if x in scores and y in scores]
    if not pairs and len(conds) >= 2:
        pairs = [(conds[0], conds[1])]
    for A, B in pairs:
        common = [i for i in ids if i in scores[A] and i in scores[B]]
        L += ["", f"## Paired comparison {B} vs {A} (n = {len(common)} requests)", "",
              "| field | b (A right, B wrong) | c (A wrong, B right) | exact McNemar p | Holm p |", "|---|---|---|---|---|"]
        res = []
        for f in metrics:
            pr = [(scores[A][i][f], scores[B][i][f]) for i in common if scores[A][i][f] is not None and scores[B][i][f] is not None]
            res.append((f,) + mcnemar([p[0] for p in pr], [p[1] for p in pr]))
        adj = holm([r[3] for r in res if r[0] not in ("event",)])
        it = iter(adj)
        for f, b, cc, p in res:
            L.append(f"| {f} | {b} | {cc} | {p:.4f} | {'primary' if f == 'event' else f'{next(it):.4f}'} |")
        for k in ("joules", "seconds", "tokens"):
            x = [cost[A][i][k] for i in common]; y = [cost[B][i][k] for i in common]
            ok = [(p, q) for p, q in zip(x, y) if p > 0 and q > 0 and not np.isnan(p) and not np.isnan(q)]
            if len(ok) > 5:
                x, y = zip(*ok)
                st = wilcoxon(x, y)
                r, lo, hi = boot_ratio(x, y)
                note = " (different machines: compares two deployments, not two model sizes)" if k == "joules" and {A, B} & {"oneprompt72b"} else ""
                L.append(f"- {k}: ratio {B}/{A} {r:.3f} (bootstrap 95% CI {lo:.3f} to {hi:.3f}), Wilcoxon W {st.statistic:.1f}, p {st.pvalue:.4g}{note}")
    if not pairs:
        L += ["", "Only one condition in the logs, so McNemar, Wilcoxon and the bootstrap ratio are skipped."]
    if pairs:
        A, B = pairs[0]
        common = [i for i in ids if i in scores[A] and i in scores[B]]
    open(os.path.join(a.out, "summary.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")

    try:
        import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib missing, plots skipped (pip install matplotlib)"); return
    fig, ax = plt.subplots(figsize=(7, 5))
    for k, c in enumerate(conds):
        rr = [r for r in rows if r["condition"] == c and r["accuracy"] != ""]
        y = np.arange(len(rr)) + k * 0.2
        acc = [r["accuracy"] for r in rr]
        ax.errorbar(acc, y, xerr=[[a_ - r["ci_low"] for a_, r in zip(acc, rr)], [r["ci_high"] - a_ for a_, r in zip(acc, rr)]], fmt="o", label=c)
        ax.set_yticks(np.arange(len(rr))); ax.set_yticklabels([r["field"] for r in rr])
    ax.set_xlim(0, 1); ax.set_xlabel("Accuracy (95% Wilson CI)"); ax.legend(frameon=False); fig.tight_layout()
    fig.savefig(os.path.join(a.out, "accuracy_per_field.png"), dpi=150); plt.close(fig)
    fig, ax = plt.subplots(figsize=(6, 5))
    if pairs:
        x = [cost[A][i]["joules"] for i in common]; y = [cost[B][i]["joules"] for i in common]
        ax.loglog(x, y, "o", alpha=.6); lim = [min(x + y), max(x + y)]; ax.plot(lim, lim, "k-", lw=.8)
        ax.set_xlabel(f"{A} J per request"); ax.set_ylabel(f"{B} J per request")
    else:
        for c in conds:
            ax.hist([cost[c][i]["joules"] for i in cost[c]], bins=20, label=c)
        ax.set_xlabel("GPU joules per request"); ax.set_ylabel("Requests"); ax.legend(frameon=False)
    fig.tight_layout(); fig.savefig(os.path.join(a.out, "energy_per_request.png"), dpi=150); plt.close(fig)
    fig, ax = plt.subplots(figsize=(7, 12))
    for k, c in enumerate(conds):
        oo = sorted([o for o in orow if o["condition"] == c], key=lambda o: (o["field"], o["option"]))
        ax.plot([o["accuracy"] for o in oo], np.arange(len(oo)) + k * .2, "o", label=c, ms=4)
        ax.set_yticks(np.arange(len(oo))); ax.set_yticklabels([f"{o['field']} {o['option']}" for o in oo], fontsize=6)
    ax.set_xlim(0, 1); ax.set_xlabel("Field accuracy per option"); ax.legend(frameon=False); fig.tight_layout()
    fig.savefig(os.path.join(a.out, "accuracy_per_option.png"), dpi=150); plt.close(fig)


if __name__ == "__main__":
    main()
