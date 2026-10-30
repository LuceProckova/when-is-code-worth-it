"""Code steps for the thesis test flow, v2 (30.10.26). The model reads the request (Intake) and picks the
calendar and people; code only does what is worth doing in code: date maths, end times, RRULE checks, titles
by the naming rules, email lookup, clash check and the write guard.

Every row carries "row" (1, 2, ...), so the model steps' tables join back by row id, never by position.

The day slot from Intake is one of:
  {"kind": "date", "value": "YYYY-MM-DD"}                  only when a full date was said
  {"kind": "monthday", "month": 11, "day": 25}             "25 November", no year
  {"kind": "relative", "weekday": "tuesday", "which": "this|next|coming"}
  {"kind": "offset", "days": 3}                            "in 3 days", "in a week" = 7
  {"kind": "nth", "n": 1, "weekday": "monday", "month": "december|this month|next month"}   n = -1 for last
  {"kind": "named", "value": "today|tomorrow|day after tomorrow|end of week|end of month|this weekend|next week"}
  {} or missing                                            no day said, so today
"""
import csv
import difflib
import os
import re
from datetime import date, datetime, timedelta

KNOW = None   # set by papertrail: My Data/knowledge
AREA = 'thesis'
DAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
MONTHS = ['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september',
          'october', 'november', 'december']
PROMPT_WORDS = r'\b(schedule|set up|add|put|create|book|make|please|can you|could you|calendar|invite|remind me)\b'


# ---------- tables ----------

def read_table(name):
    try:
        with open(os.path.join(KNOW, AREA, name + '.csv'), encoding='utf-8-sig', newline='') as f:
            return [{k: (v or '').strip() for k, v in r.items()} for r in csv.DictReader(f)]
    except FileNotFoundError:
        return []


def split_list(v):
    return [x.strip().lower() for x in (v or '').split(';') if x.strip()]


def setting(name, default=''):
    return next((r['value'] for r in read_table('settings') if r['setting'] == name), default)


def now_of(ctx):
    t = ctx.get('test_today')
    return datetime.strptime(t, '%Y-%m-%d %H:%M') if t else datetime.now()


def how(ctx, text):
    ctx.setdefault('_how', []).append(text)


def done(ctx, out):
    ctx['_note'] = ' | '.join(x for x in ctx.pop('_how', []) if x)
    return out


def ask(ctx, text, field=None):
    if field:
        ctx.setdefault('asked_fields', set()).add(field)
    if not isinstance(ctx.get('asks'), list):
        ctx['asks'] = []
    if text not in ctx['asks']:
        ctx['asks'].append(text)


def flag(e, k):
    return str(e.get(k)).strip().lower() == 'true'


def label(e):
    return e.get('title') or e.get('what') or 'this event'


# ---------- (a) date maths ----------

def _wd(name):
    n = (name or '').strip().lower()[:3]
    return next((i for i, d in enumerate(DAYS) if d.startswith(n)), None) if n else None


def _month(name):
    n = str(name or '').strip().lower()
    if n.isdigit():
        return int(n)
    return next((i + 1 for i, m in enumerate(MONTHS) if n[:3] and m.startswith(n[:3])), None)


def _valid(y, m, d):
    try:
        return date(y, m, d)
    except ValueError:
        return None


def nth_weekday(y, mo, n, wd):
    days = [d for d in (_valid(y, mo, i) for i in range(1, 32)) if d and d.weekday() == wd]
    if n == -1 or n > len(days):
        return days[-1]
    return days[n - 1]


def resolve_day(day, now):
    """The structured day slot as a date. today is the request's date, now its date and time."""
    today = now.date() if isinstance(now, datetime) else now
    d = day if isinstance(day, dict) else {}
    k = (d.get('kind') or '').lower()
    if not k:
        return today
    if k == 'date':
        x = date.fromisoformat(str(d['value'])[:10])
        if x >= today:
            return x
        d, k = {'month': x.month, 'day': x.day}, 'monthday'   # the model added a year that was not said
    if k == 'offset':
        return today + timedelta(days=int(d.get('days') or 0))
    if k == 'monthday':
        mo, dd = _month(d.get('month')), int(d.get('day'))
        x = date(today.year, mo, dd)
        return x if x >= today else date(today.year + 1, mo, dd)
    if k == 'relative':
        wd = _wd(d.get('weekday'))
        if wd is None:
            raise ValueError('no weekday')
        ahead = (wd - today.weekday()) % 7
        which = (d.get('which') or 'this').lower()
        if which == 'next' and setting('next_means', 'coming') == 'week_after':
            ahead += 7 if ahead else 7
        elif ahead == 0 and which in ('next', 'coming'):
            ahead = 7
        return today + timedelta(days=ahead)
    if k == 'nth':
        wd, n = _wd(d.get('weekday')), int(d.get('n') or 1)
        m = str(d.get('month') or 'this month').lower()
        if m == 'next month':
            y, mo = today.year + (today.month == 12), today.month % 12 + 1
        elif m in ('this month', 'the month', 'month', 'each month', 'every month'):
            y, mo = today.year, today.month
        else:
            mo = _month(m)
            y = today.year if mo >= today.month else today.year + 1
        x = nth_weekday(y, mo, n, wd)
        if x < today:   # already gone: the next month (or next year for a named month)
            if m in ('this month', 'the month', 'month', 'each month', 'every month'):
                y, mo = y + (mo == 12), mo % 12 + 1
            else:
                y += 1
            x = nth_weekday(y, mo, n, wd)
        return x
    if k == 'named':
        v = str(d.get('value') or '').lower().replace('the ', '').strip()
        if v in ('today', 'tonight', 'this evening', 'this morning', 'this afternoon'):
            return today
        if v == 'tomorrow':
            early = isinstance(now, datetime) and now.strftime('%H:%M') < setting('tomorrow_means_today_before', '00:00')
            return today if early else today + timedelta(days=1)
        if v == 'day after tomorrow':
            return today + timedelta(days=2)
        if v in ('end of week', 'end of the week'):
            return today + timedelta(days=(4 - today.weekday()) % 7) if today.weekday() <= 4 else today + timedelta(days=6 - today.weekday())
        if v in ('end of month', 'end of the month'):
            return date(today.year + (today.month == 12), today.month % 12 + 1, 1) - timedelta(days=1)
        if v in ('next week', 'sometime next week'):
            return today + timedelta(days=7 - today.weekday())
        if v in ('this weekend', 'weekend'):
            return today if today.weekday() == 6 else today + timedelta(days=(5 - today.weekday()) % 7)
    raise ValueError('cannot read the day %s' % d)



NUMS = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10, 'a': 1}


def words_day(words, now):
    """The day words exactly as said, read by code where the meaning is fixed ("next tuesday", "in 3 days",
    "25 november", "first monday of december"). None when the words are not one of those, then the model's slot is used."""
    w = re.sub(r'^(on|the|for)\s+', '', re.sub(r'\s+', ' ', str(words or '').strip().lower())).strip(' ,.')
    if not w:
        return None
    w = re.sub(r'\b(\d+)(st|nd|rd|th)\b', r'\1', w)
    m = re.match(r'^in (\w+) (day|days|week|weeks)$', w)
    if m:
        n = int(m.group(1)) if m.group(1).isdigit() else NUMS.get(m.group(1))
        return None if n is None else {'kind': 'offset', 'days': n * (7 if m.group(2).startswith('w') else 1)}
    if w in ('today', 'tonight', 'this evening', 'this afternoon', 'this morning', 'tomorrow', 'day after tomorrow',
             'the day after tomorrow', 'next week', 'sometime next week', 'this weekend', 'end of the week', 'end of week',
             'end of the month', 'end of month'):
        return {'kind': 'named', 'value': w.replace('the day after', 'day after').replace('sometime ', '')}
    m = re.match(r'^(this |next |coming |on )?(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)s?$', w)
    if m:
        return {'kind': 'relative', 'weekday': m.group(2), 'which': (m.group(1) or 'this').strip().replace('on', 'this')}
    m = re.match(r'^(\d{4}-\d{2}-\d{2})$', w)
    if m:
        return {'kind': 'date', 'value': m.group(1)}
    w = w.replace(' of ', ' ')   # "25 of november", "first friday of december"
    m = re.match(r'^(\d{1,2}) ([a-z]+)$', w) or re.match(r'^([a-z]+) (\d{1,2})$', w)
    if m:
        a, b = m.groups()
        dd, mo = (a, b) if a.isdigit() else (b, a)
        if _month(mo):
            return {'kind': 'monthday', 'month': _month(mo), 'day': int(dd)}
    m = re.match(r'^(first|second|third|fourth|last) ([a-z]+) (in |of )?(.+)$', w)
    if m and _wd(m.group(2)) is not None:
        return {'kind': 'nth', 'n': {'first': 1, 'second': 2, 'third': 3, 'fourth': 4, 'last': -1}[m.group(1)], 'weekday': m.group(2), 'month': m.group(4)}
    return None



def said_clock(text):
    """A clock time said in words, read by code: "3 pm", "15:00", "half 3", "quarter to 1", "noon". Bare hours 1 to 7 are pm."""
    t = (text or '').lower()
    w = re.search(r'\bbetween (\d{1,2}(?::\d{2})?)\s*(am|pm)? and \d{1,2}(?::\d{2})?\s*(am|pm)?', t)
    if w:   # a window starts at its first time, with the am or pm of the window
        ap = w.group(2) or w.group(3) or ('pm' if int(w.group(1).split(':')[0]) <= 7 else 'am')
        t = w.group(1) + ' ' + ap
    m = re.search(r'\b(noon|midday)\b', t)
    if m:
        return '12:00'
    m = re.search(r'\bhalf (?:past )?(\d{1,2})\b', t)
    if m:
        h = int(m.group(1)); h = h + 12 if 1 <= h <= 7 else h
        return '%02d:30' % h
    m = re.search(r'\bquarter (to|past) (\d{1,2})\b', t)
    if m:
        h = int(m.group(2)); h = h + 12 if 1 <= h <= 7 else h
        return ('%02d:45' % ((h - 1) % 24)) if m.group(1) == 'to' else '%02d:15' % h
    m = re.search(r'\b(\d{1,2}):(\d{2})\s*(am|pm|a\.m\.|p\.m\.)?', t) or re.search(r'\b(\d{1,2})()\s*(am|pm|a\.m\.|p\.m\.)', t)
    if m:
        h = int(m.group(1)); ap = (m.group(3) or '').replace('.', '')
        if ap == 'pm' and h < 12: h += 12
        if ap == 'am' and h == 12: h = 0
        if not ap and not m.group(2) and 1 <= h <= 7: h += 12
        return '%02d:%s' % (h, m.group(2) or '00') if h < 24 else None
    return None


REPEAT_WORDS = [
    (r'\bevery (week)?day\b|\bdaily\b|\bevery morning\b|\bevery evening\b', 'FREQ=DAILY'),
    (r'\bevery (weekday|working day)\b|\bmonday to friday\b|\bon weekdays\b', 'FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR'),
    (r'\bevery (other|second) (week|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b|\bfortnightly\b|\bevery 2 weeks\b|\bevery two weeks\b', 'FREQ=WEEKLY;INTERVAL=2'),
    (r'\bsame weekday every month\b', 'MONTHLY_BYDAY'),
    (r'\bevery month\b|\bmonthly\b', 'FREQ=MONTHLY'),
    (r'\bevery year\b|\byearly\b|\bannually\b', 'FREQ=YEARLY'),
    (r'\bfor (\d+|two|three|four|five|six) weeks\b', 'COUNT_WEEKS'),
    (r'\bevery (week|monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?\b|\bweekly\b|\bonce a week\b', 'FREQ=WEEKLY'),
]


def said_repeat(text, start):
    """The repeat said in words, read by code where the phrase has one meaning. None when no repeat is said."""
    t = (text or '').lower()
    t = re.sub(r'"[^"]*"', '', t)   # a quoted message is not a repeat
    for pat, rule in REPEAT_WORDS:
        m = re.search(pat, t)
        if not m:
            continue
        if rule == 'MONTHLY_BYDAY':
            n = (start.day - 1) // 7 + 1
            return 'FREQ=MONTHLY;BYDAY=%s%s' % (-1 if n == 5 else n, ['MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU'][start.weekday()])
        if rule == 'COUNT_WEEKS':
            n = m.group(1); n = int(n) if n.isdigit() else NUMS.get(n, 4)
            return 'FREQ=WEEKLY;COUNT=%d' % n
        if rule == 'FREQ=MONTHLY':
            return 'FREQ=MONTHLY;BYMONTHDAY=%d' % start.day
        return rule
    return None


def said_reminder(text):
    t = (text or '').lower()
    if not re.search(r'\bremind', t):
        return None
    if re.search(r'day before|a day', t):
        return 1440
    m = re.search(r'(\d+|an?|one|two) (hour|hours|minute|minutes|min|mins) before', t)
    if m:
        n = 1 if m.group(1) in ('a', 'an', 'one') else (2 if m.group(1) == 'two' else int(m.group(1)))
        return n * 60 if m.group(2).startswith('h') else n
    if re.search(r'week before', t):
        return 10080
    return 1440


def hhmm(v):
    m = re.match(r'^(\d{1,2}):(\d{2})$', str(v or '').strip())
    return '%02d:%s' % (int(m.group(1)), m.group(2)) if m and int(m.group(1)) < 24 else None


def add_minutes(day, start, minutes):
    """(b) end = start + minutes, the end date moves on when it crosses midnight."""
    a = datetime.strptime(day + ' ' + start, '%Y-%m-%d %H:%M') + timedelta(minutes=int(minutes))
    return a.strftime('%H:%M'), a.strftime('%Y-%m-%d')


TEXT_SLOTS = ('what', 'day_words', 'start', 'end', 'after_event', 'part_of_day', 'where', 'repeat', 'until_words', 'notes', 'ask', 'minutes')


def tidy(e):
    """The model sometimes wraps a plain value as {"value": x} or gives {} for nothing: one shape for every slot."""
    for k in TEXT_SLOTS:
        v = e.get(k)
        if isinstance(v, dict):
            e[k] = next((x for x in v.values() if isinstance(x, (str, int, float))), '') if v else ''
        elif isinstance(v, list) and k != 'who':
            e[k] = ' '.join(str(x) for x in v)
    if not isinstance(e.get('who'), list):
        e['who'] = [e['who']] if str(e.get('who') or '').strip() else []
    return e


def resolve_when(events, ctx):
    """Date from the structured day and the test row's today, start as the model gave it (or the end of the event
    it comes after), end from minutes or the end time, contradictions ask."""
    now = now_of(ctx)
    existing = read_table('existing-events')
    known = {r['activity'].lower(): int(r['minutes']) for r in read_table('durations') if r.get('minutes', '').isdigit()}
    out = []
    for e in events or []:
        e = tidy(dict(e))
        # code reads the day words itself where their meaning is fixed, the model's slot is the fallback
        slot = words_day(e.get('day_words'), now) or e.get('day')
        if not words_day(e.get('day_words'), now) and str(e.get('day_words') or '').strip() and not (e.get('day') or {}).get('kind'):
            ask(ctx, 'Which day is %s? You said "%s".' % (label(e), e.get('day_words')), 'date')
        try:
            e['date'] = resolve_day(slot, now).isoformat()
        except (ValueError, TypeError, KeyError):
            e['date'] = None
            ask(ctx, 'Which day is %s?' % label(e), 'date')
        start, end = hhmm(e.get('start')), hhmm(e.get('end'))
        start = hhmm(said_clock(e.get('start_words'))) or start   # code reads the time words, the model's HH:MM is the fallback
        after = str(e.get('after_event') or '').strip().lower()
        if after and e['date']:
            key = re.sub(r'\b(my|the|appointment)\b', '', after).strip()[:5]
            hits = [x for x in existing if key and key in x['title'].lower()]
            hit = next((x for x in hits if x['date'] == e['date']), None)
            if not hit and not (e.get('day') or {}).get('kind'):
                hit = next((x for x in sorted(hits, key=lambda x: x['date']) if x['date'] >= now.date().isoformat()), None)
                if hit:
                    e['date'] = hit['date']
            if hit and start and start != hit['end']:
                ask(ctx, '%s: after %s (%s), or at %s?' % (label(e), after, hit['end'], start))
            elif hit:
                start = hit['end']
            elif not start:
                ask(ctx, 'Which event is "%s" on %s?' % (after, e['date']), 'start')
        all_day = flag(e, 'all_day') or bool(re.search(r'\b(all|whole) day\b', ctx.get('input') or '', re.I))
        guess = str(e.get('part_of_day') or '').strip().lower()
        said = ctx.get('input') or ''
        # a half day only counts as a length when it is said as one ("for the morning", "the whole afternoon");
        # "in the evening" is just a start time, so the model's guess never sets an end
        m = re.search(r'\b(for|keep|the whole|whole|all) (the )?(morning|afternoon|evening)\b', said, re.I)
        part = m.group(3).lower() if m else ''
        if not part and guess in ('morning', 'afternoon', 'evening') and not start:
            start = setting('half_day_' + guess, '08:00-12:00').split('-')[0]
        m = re.search(r'\bat (noon|midday|(\d{1,2})(?::(\d{2}))?\s*(am|pm)?)\b', said, re.I)
        if m and start:
            if m.group(1).lower() in ('noon', 'midday'):
                said_start = '12:00'
            else:
                h = int(m.group(2)); ap = (m.group(4) or '').lower()
                h = h + 12 if (ap == 'pm' or (not ap and 1 <= h <= 7)) and h < 12 else h
                said_start = '%02d:%s' % (h, m.group(3) or '00')
            if said_start != start:
                ask(ctx, '%s: at %s, or at %s?' % (label(e), said_start, start))
        m = re.search(r'\bfrom (\d{1,2})(?::(\d{2}))?\s*(am|pm)? (?:to|till|until|-) (\d{1,2})(?::(\d{2}))?\s*(am|pm)?', said, re.I)
        if m and start:   # a range said in words must agree with the start
            h = int(m.group(1)); ap = (m.group(3) or m.group(6) or '').lower()
            h = h + 12 if (ap == 'pm' or (not ap and 1 <= h <= 7)) and h < 12 else h
            rs = '%02d:%s' % (h, m.group(2) or '00')
            if rs != start:
                ask(ctx, '%s: from %s, or at %s?' % (label(e), rs, start))
        if all_day and (start or end or part):
            ask(ctx, '%s: all day, or %s?' % (label(e), 'from ' + start if start else 'the ' + part if part else 'until ' + end))
        if part in ('morning', 'afternoon', 'evening') and not all_day:
            a, b = setting('half_day_' + part, '08:00-12:00').split('-')
            if (start and not (a <= start < b)) or (end and not (a < end <= b)):
                ask(ctx, '%s: the %s (%s to %s), or from %s?' % (label(e), part, a, b, start or end))
            start, end = start or a, end or b
        mins = e.get('minutes')
        try:
            mins = int(float(mins)) if mins not in (None, '') else None
        except ValueError:
            mins = None
        e['end_date'] = e['date']
        if all_day:
            e.update(allDay=True, start=None, end=None)
        elif start and e['date']:
            if end and mins:
                calc, _ = add_minutes(e['date'], start, mins)
                if calc != end:
                    ask(ctx, '%s: until %s, or for %d minutes?' % (label(e), end, mins))
            if not end:
                if not mins:
                    what = (e.get('what') or '').lower()
                    mins = next((m for a, m in sorted(known.items(), key=lambda x: -len(x[0])) if re.search(r'\b' + re.escape(a) + r'\b', what)),
                                int(setting('default_minutes', '30')))
                end, e['end_date'] = add_minutes(e['date'], start, mins)
            elif end <= start:   # ends after midnight
                e['end_date'] = (date.fromisoformat(e['date']) + timedelta(days=1)).isoformat()
            e.update(allDay=False, start=start, end=end, minutes=mins)
        else:
            e.update(allDay=False, start=None, end=None)
        how(ctx, '%s from %s is %s, %s' % (e.get('day') or 'no day', now.strftime('%a %d %b'), e['date'],
                                           'all day' if e.get('allDay') else '%s to %s' % (e['start'], e['end'])))
        out.append(e)
    return done(ctx, out)


# ---------- (c) repeat ----------

def until_date(words, start):
    w = re.sub(r'^(until|till|to|through)\s+', '', (words or '').strip().lower()).replace('the ', '').strip()
    if not w:
        return None
    if w in ('christmas', 'christmas day'):
        x = date(start.year, 12, 25)
    elif w in ('end of year', 'end of the year', 'new year', "new year's eve"):
        x = date(start.year, 12, 31)
    elif re.match(r'^\d{4}-\d{2}-\d{2}$', w):
        x = date.fromisoformat(w)
    else:
        m = re.match(r'^end of ([a-z]+)$', w)
        if m and _month(m.group(1)):
            mo = _month(m.group(1))
            y = start.year if mo >= start.month else start.year + 1
            return date(y + (mo == 12), mo % 12 + 1, 1) - timedelta(days=1)
        m = re.match(r'^(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?([a-z]+)$', w) or re.match(r'^([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?$', w)
        if not m:
            raise ValueError(words)
        a, b = m.groups()
        dd, mo = (a, b) if a.isdigit() else (b, a)
        x = date(start.year, _month(mo), int(dd))
    return x if x >= start else date(x.year + 1, x.month, x.day)


def resolve_repeat(events, ctx):
    """The model's RRULE checked with dateutil, UNTIL worked out from the until words and after the start."""
    from dateutil.rrule import rrulestr
    out = []
    for e in events or []:
        e = dict(e)
        rule = re.sub(r'^RRULE:', '', str(e.get('repeat') or '').strip(), flags=re.I).strip(' ;').upper()
        rule = rule.replace('FREQ=BIWEEKLY', 'FREQ=WEEKLY;INTERVAL=2')
        if e.get('date'):
            words_rule = said_repeat(ctx.get('input') or '', date.fromisoformat(e['date']))
            if words_rule:
                rule = words_rule   # a repeat phrase with one meaning is read by code
        rule = ';'.join(p for p in rule.split(';') if p and not p.startswith('UNTIL='))
        # day names as RFC 5545 two-letter codes (the model sometimes writes TUE or TUESDAY)
        rule = re.sub(r'BYDAY=([^;]*)', lambda b: 'BYDAY=' + re.sub(r'([+-]?\d*)(MON|TUE|WED|THU|FRI|SAT|SUN)[A-Z]*', lambda m: m.group(1) + m.group(2)[:2], b.group(1)), rule)
        words = str(e.get('until_words') or '').strip()
        e['rrule'] = None
        if words and not rule:
            ask(ctx, 'How often does %s repeat?' % label(e), 'rrule')
        elif rule and e.get('date'):
            start = date.fromisoformat(e['date'])
            try:
                if words:
                    u = until_date(words, start)
                    rule += ';UNTIL=%s' % u.strftime('%Y%m%d')
                rrulestr('RRULE:' + rule, dtstart=datetime.combine(start, datetime.min.time()))
                e['rrule'] = rule
            except Exception:
                ask(ctx, 'How often does %s repeat, and until when?' % label(e), 'rrule')
        how(ctx, e['rrule'] or ('no repeat' if not rule else 'unreadable repeat'))
        out.append(e)
    return done(ctx, out)


# ---------- (d) title ----------

def make_title(events, ctx):
    """The title from what + who by naming.csv, company short names from companies.csv, MAYBE, TEMP, PH."""
    pats = {r['rule']: r['pattern'] for r in read_table('naming')}
    companies = read_table('companies')
    out = []
    for e in events or []:
        e = dict(e)
        what = (e.get('what') or '').strip()
        if str(e.get('ask') or '').strip():
            ask(ctx, str(e['ask']).strip(), 'what')
        what = re.sub(r'^((please|can you|could you|schedule|set up|add|put|create|book|make|block|uh+|um+|the|a)\s+)+', '', what, flags=re.I).strip(' ,.')
        what = what[:1].upper() + what[1:]
        if not what:
            ask(ctx, 'What is the event?', 'what')
        elif re.search(PROMPT_WORDS, what, re.I):
            ask(ctx, 'What should "%s" be called?' % what, 'title')
        who = [re.sub(r'\s*\(.*\)$', '', p) for p in (e.get('people') or e.get('who') or []) if str(p).strip()]
        places = {n for r in read_table('places') for n in [r['place'].lower()] + split_list(r.get('also_written_as'))}
        who = [w for w in who if w.strip() and w.lower() not in what.lower() and w.lower() not in ('the team', 'the client') and w.lower() not in places]
        title = pats['with_who'].replace('{What}', what).replace('{who}', ' and '.join(who)) if who else pats['plain'].replace('{What}', what)
        text = (what + ' ' + ' '.join(e.get('who') or []) + ' ' + ' '.join(e.get('people') or [])).lower()
        co = next((c for c in companies if any(re.search(r'\b' + re.escape(k) + r'\b', text) for k in split_list(c['keywords']))), None)
        if co and flag(e, 'not_agreed'):
            title = pats['not_agreed'].replace('{SHORT}', co['short']).replace('{title}', title)
        elif co and flag(e, 'booked_no_invite'):
            title = pats['booked_no_invite'].replace('{SHORT}', co['short']).replace('{title}', title)
        else:
            if co:
                title = pats['company'].replace('{SHORT}', co['short']).replace('{title}', title)
            if flag(e, 'maybe'):
                title = pats['maybe'].replace('{title}', title)
        how(ctx, title)
        out.append(dict(e, title=title))
    return done(ctx, out)


# ---------- (e)(f) calendar, people, clash ----------

def check_event(events, ctx):
    """Calendar name must exist, people get their emails by exact name, clashes with existing events ask,
    "check with me first" asks. The rows come in already joined with the picks and notes tables."""
    cals = [r['calendar'] for r in read_table('calendars')]
    people = {r['person'].lower(): r for r in read_table('people')}
    existing = read_table('existing-events')
    anyway = re.search(r'book (it )?anyway|anyway', ctx.get('input') or '', re.I)
    out = []
    for e in events or []:
        e = dict(e)
        c = str(e.get('calendar') or '').strip()
        if c not in cals:
            opts = [x.strip() for x in re.split(r'\s+or\s+|,', c) if x.strip() in cals] or difflib.get_close_matches(c, cals, n=2, cutoff=0.5)
            ask(ctx, 'Which calendar is %s on? %s' % (label(e), ' or '.join(opts) if len(opts) > 1 else (opts[0] + '?' if opts else '')), 'calendar')
            c = opts[0] if len(opts) == 1 else None
        e['calendar'] = c
        att = []
        place_names = {n for r in read_table('places') for n in [r['place'].lower()] + split_list(r.get('also_written_as'))}
        for p in e.get('people') or []:
            if not str(p).strip() or str(p).strip().lower() in place_names or str(p).strip() in cals:
                continue   # empty, or a place or calendar the model put among the people
            r = people.get(str(p).strip().lower())
            if not r:
                base = re.sub(r'\s*\(.*\)$', '', str(p)).strip().lower()
                r = people.get(base) or next((x for x in people.values() if base in split_list(x.get('also_written_as'))), None)
            if not r:   # a name as said: the table's other spellings, two of them = the one on this event's calendar
                lp = str(p).strip().lower()
                hits = [x for x in people.values() if lp in split_list(x.get('also_written_as')) or lp == re.sub(r'\s*\(.*\)$', '', x['person']).lower()]
                hits = [x for x in hits if x['calendar'] == c] if len(hits) > 1 else hits
                r = hits[0] if len(hits) == 1 else None
            if r:
                att.append({'name': re.sub(r'\s*\(.*\)$', '', r['person']), 'email': r['email']})
            elif '@' in str(p):
                att.append({'name': p, 'email': p})
            else:
                ask(ctx, 'Who is %s?' % p, 'attendees')
        e['attendees'] = att
        if e.get('date') and e.get('start') and not anyway:
            for x in existing:
                if x['date'] == e['date'] and x['start'] < (e['end'] if e.get('end_date') == e['date'] else '24:00') and e['start'] < x['end']:
                    ask(ctx, '%s clashes with %s (%s to %s). Book anyway?' % (label(e), x['title'], x['start'], x['end']))
        if flag(e, 'confirm_first'):
            ask(ctx, 'Shall I put in %s on %s?' % (label(e), e.get('date')))
        how(ctx, '%s, %s' % (c or '?', ', '.join(a['name'] for a in att) or 'nobody'))
        keep = ['row', 'title', 'date', 'end_date', 'start', 'end', 'allDay', 'location', 'attendees', 'calendar', 'rrule', 'desc', 'reminder_minutes']
        e['location'] = place_of(e.get('where'))
        e['reminder_minutes'] = said_reminder(ctx.get('input'))   # code reads "remind me ...", never invented
        out.append({k: e.get(k) for k in keep})
    return done(ctx, out)


def place_of(where):
    """The canonical place from places.csv when the words are one of its spellings, else the words as said."""
    w = str(where or '').split(';')[0].strip()
    if not w:
        return None
    if '->' in w or re.search(r'https?://', w):
        return w
    low = re.sub(r'^(the|at|in|on)\s+', '', w.lower())
    for r in read_table('places'):
        names = [r['place'].lower()] + split_list(r.get('also_written_as'))
        if low in [re.sub(r'^(the|at|in|on)\s+', '', n) for n in names]:
            return r['place']
    return w


# ---------- (g) the write guard, inside the create action ----------

def test_send(rows, ctx):
    """In the thesis test every event goes into the one test calendar, titled TEST, with the people in the notes
    (never as invitees). Refuses anything else."""
    cal_id = setting('test_calendar_id')
    out = []
    for e in rows or []:
        desc = '\n'.join(x for x in [e.get('desc') or '', 'With: ' + ', '.join(a['name'] for a in e.get('attendees') or []) if e.get('attendees') else ''] if x)
        s = dict({k: v for k, v in e.items() if k not in ('attendees', 'row', 'calendar', 'end_date')},
                 title='TEST %s - %s [%s]' % (ctx.get('test_id', ''), e['title'], e.get('calendar') or '?'), desc=desc, calId=cal_id)
        out.append(s)
    for s in out:
        if not s['title'].startswith('TEST ') or (not cal_id and not ctx.get('dry')) or s['calId'] != cal_id or s.get('attendees'):
            raise PermissionError('write guard: only TEST events into test_calendar_id from settings.csv')
    return out


STEPS = {'thesis_when': resolve_when, 'thesis_repeat': resolve_repeat, 'thesis_title': make_title,
         'thesis_check': check_event, 'thesis_test_send': test_send}
