"""code steps for the thesis test flow, model reads the request, code does the fixed maths and lookups

day slot from intake:
  {"kind": "date", "value": "YYYY-MM-DD"}                  only when a full date was said
  {"kind": "monthday", "month": 11, "day": 25}             "25 November", no year
  {"kind": "relative", "weekday": "tuesday", "which": "this|next|coming"}
  {"kind": "offset", "days": 3}                            "in 3 days", "in a week" = 7
  {"kind": "nth", "n": 1, "weekday": "monday", "month": "december|this month|next month"}   n = -1 for last
  {"kind": "named", "value": "today|tomorrow|day after tomorrow|end of week|end of month|this weekend|next week"}
  {} or missing                                            no day said, so today
"""
import csv
import json
import difflib
import os
import re
from datetime import date, datetime, timedelta

KNOW = None   # set by papertrail
AREA = 'tables'
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
    today = now.date() if isinstance(now, datetime) else now
    d = day if isinstance(day, dict) else {}
    k = (d.get('kind') or '').lower()
    if not k:
        return today
    if k == 'date':
        x = date.fromisoformat(str(d['value'])[:10])
        if x >= today:
            return x
        d, k = {'month': x.month, 'day': x.day}, 'monthday'   # model added a year nobody said
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
        if x < today:   # already gone, roll to next month or year
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
    """day words code can read, else none and the model slot is used"""
    w = re.sub(r'^(on|the|for)\s+', '', re.sub(r'\s+', ' ', str(words or '').strip().lower())).strip(' ,.')
    w = re.sub(r'\s+(early|late|in the (morning|afternoon|evening)|morning|afternoon|evening|night|after .*|at .*)$', '', w).strip(' ,.')
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
    w = w.replace(' of ', ' ')
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



STREET = r'(road|street|st|avenue|ave|lane|square|way|close|drive|place)\b'
MONTH_RE = r'(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*'


def no_dates(t):
    """strip dates so 2026-11-16 never reads as a time"""
    t = re.sub(r'\b\d{4}-\d{2}-\d{2}\b', ' ', t)
    t = re.sub(r'\b\d{1,2}(st|nd|rd|th)?\s+(of\s+)?' + MONTH_RE + r'\b', ' ', t)
    return re.sub(r'\b' + MONTH_RE + r'\s+\d{1,2}(st|nd|rd|th)?\b', ' ', t)
RANGE = r'(\d{1,2}(?::\d{2})?)\s*(am|pm)?\s*(?:and|to|till|until|-)\s*(\d{1,2}(?::\d{2})?)\s*(am|pm)?'


def said_clock(text):
    """clock time from words, bare 1 to 7 is pm, a range starts at its first time"""
    t = no_dates((text or '').lower())
    t = re.sub(r'\b\d{1,3}\s+[a-z]+\s+' + STREET, ' ', t)
    w = re.search(r'\b(?:between|from)\s+' + RANGE, t) or re.search(r'\b' + RANGE.replace('(?:and|to|till|until|-)', '(?:to|-)'), t)
    if w:
        h, mi = (w.group(1) + ':00').split(':')[:2]
        h, ap = int(h), w.group(2) or w.group(4) or ''
        end_h = int(w.group(3).split(':')[0])
        if ap == 'pm' and h < 12 and not (w.group(4) == 'pm' and not w.group(2) and h > end_h):
            h += 12
        elif not ap and 1 <= h <= 7:
            h += 12
        return '%02d:%s' % (h, mi) if h < 24 else None
    else:
        t = re.sub(r'\b(until|till)\s+\S+(\s*(am|pm))?', ' ', t)
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
    (r'\bevery (weekday|working day)\b|\bmonday to friday\b|\bon weekdays\b', 'FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR'),
    (r'\bevery day\b|\bdaily\b|\bevery morning\b|\bevery evening\b', 'FREQ=DAILY'),
    (r'\bevery (other|second) (week|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b|\bfortnightly\b|\bevery 2 weeks\b|\bevery two weeks\b', 'FREQ=WEEKLY;INTERVAL=2'),
    (r'\bsame weekday every month\b', 'MONTHLY_BYDAY'),
    (r'\bevery month\b|\bmonthly\b', 'FREQ=MONTHLY'),
    (r'\bevery year\b|\byearly\b|\bannually\b', 'FREQ=YEARLY'),
    (r'\bfor (\d+|two|three|four|five|six) weeks\b', 'COUNT_WEEKS'),
    (r'\bevery (week|monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?\b|\bweekly\b|\bonce a week\b', 'FREQ=WEEKLY'),
]


def said_repeat(text, start):
    """repeat phrase with one meaning, else none"""
    t = (text or '').lower()
    t = re.sub(r'"[^"]*"', '', t)   # quoted message not a repeat
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
    """end date rolls over past midnight"""
    a = datetime.strptime(day + ' ' + start, '%Y-%m-%d %H:%M') + timedelta(minutes=int(minutes))
    return a.strftime('%H:%M'), a.strftime('%Y-%m-%d')


TEXT_SLOTS = ('what', 'day_words', 'start', 'end', 'after_event', 'part_of_day', 'where', 'repeat', 'until_words', 'notes', 'ask', 'minutes')


def tidy(e):
    """model wraps values as {"value": x} or {}, flatten every slot"""
    for k in TEXT_SLOTS:
        v = e.get(k)
        if isinstance(v, dict):
            e[k] = next((x for x in v.values() if isinstance(x, (str, int, float))), '') if v else ''
        elif isinstance(v, list) and k != 'who':
            e[k] = ' '.join(str(x) for x in v)
    if not isinstance(e.get('who'), list):
        e['who'] = [e['who']] if str(e.get('who') or '').strip() else []
    return e


SOFT_STARTS = [   # longest phrases first
    (r'\bafter breakfast\b', 'after_breakfast_start'), (r'\bafter lunch\b', 'after_lunch_start'),
    (r'\bafter dinner\b', 'after_dinner_start'), (r'\blate morning\b', 'late_morning_start'), (r'\blunch ?time\b', 'lunchtime'),
    (r'\bin the morning\b', 'morning_start'), (r'\bin the afternoon\b', 'afternoon_start'), (r'\bin the evening\b', 'evening_start'),
    (r'\bearly\b', 'early_start'),
]
NOT_EVENT = r'^(remind(er)?|remind me|door code|code|note|notes|bring|to do|todo|agenda|plan|reminder for|send( an?)? invite|invite)\b'


def soft_start(text, what=''):
    """soft start from settings, early lunch uses lunch window"""
    t = (text or '').lower()
    for pat, key in SOFT_STARTS:
        if re.search(pat, t):
            if key == 'early_start' and re.search(r'\blunch\b', (what or '').lower()):
                return setting('lunch_window', '11:30-14:30').split('-')[0]
            v = hhmm(setting(key))
            if v:
                return v
    return None


def as_hhmm(v):
    """model time in any shape to hh:mm"""
    if isinstance(v, dict):
        if 'hour' in v:
            try:
                return '%02d:%02d' % (int(v['hour']), int(v.get('minute') or 0))
            except (TypeError, ValueError):
                return None
        if v.get('kind') == 'setting':
            return hhmm(setting(str(v.get('value'))))
        return next((hhmm(x) for x in v.values() if hhmm(x)), None)
    return hhmm(v)


LENGTH = re.compile(r'\b(?:for\s+)?(an hour and a half|one and a half hours|half an hour|half hour|quarter of an hour|an hour|one hour|a hour|'
                    r'(\d+(?:[.,]\d+)?)\s*(hours?|hrs?|h|minutes?|mins?))\b', re.I)


def said_minutes(text):
    """length said in request, quick = quick_minutes"""
    t = re.sub(r'"[^"]*"', '', (text or '').lower())
    t = re.sub(r'\b(every|in|for)\s+\d+\s+(weeks?|days?|months?)\b', ' ', t)
    t = re.sub(r'\bremind(er)?\b[^.]*', ' ', t)   # remind me an hour before is the reminder not the length
    m = LENGTH.search(t)
    if m:
        words = {'an hour and a half': 90, 'one and a half hours': 90, 'half an hour': 30, 'half hour': 30,
                 'quarter of an hour': 15, 'an hour': 60, 'one hour': 60, 'a hour': 60}
        if m.group(1) in words:
            return words[m.group(1)]
        n = float(m.group(2).replace(',', '.'))
        return int(n * 60) if m.group(3).startswith('h') else int(n)
    if re.search(r'\bquick\b', t):
        return int(setting('quick_minutes', '15'))
    return None


def _clock(h, mi, ap):
    h = int(h)
    if ap == 'pm' and h < 12:
        h += 12
    elif not ap and 1 <= h <= 7:
        h += 12
    return '%02d:%s' % (h, mi or '00') if h < 24 else None


def said_end(text):
    """end and window end, between only counts with no length"""
    t = re.sub(r'\b\d{1,3}\s+[a-z]+\s+' + STREET, ' ', no_dates((text or '').lower()))
    m = re.search(r'\b(?:until|till)\s+(midnight|noon)\b', t)
    if m:
        return ('00:00' if m.group(1) == 'midnight' else '12:00'), None
    m = re.search(r'\b(?:until|till)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', t)
    if m:
        return _clock(m.group(1), m.group(2), m.group(3)), None
    m = re.search(r'\bbetween\s+' + RANGE, t)
    if m:
        h, mi = (m.group(3) + ':00').split(':')[:2]
        return None, _clock(h, mi, m.group(4) or m.group(2))
    m = re.search(r'\b(?:from\s+)?' + RANGE.replace('(?:and|to|till|until|-)', '(?:to|-)'), t)
    if m:
        h, mi = (m.group(3) + ':00').split(':')[:2]
        return _clock(h, mi, m.group(4) or m.group(2)), None
    return None, None


def after_event_said(text):
    """after my x, not meals"""
    m = re.search(r'\bafter (?:my|the) ([a-z][a-z ]*?)(?=\s+(?:for|at|on|in|until|till|from|with|to|every|and)\b|[,.]|$)', (text or '').lower())
    if m and m.group(1).strip() not in ('breakfast', 'lunch', 'dinner', 'meal'):
        return m.group(1).strip()
    return ''


DAY_PHRASES = [r'\d{4}-\d{2}-\d{2}', r'(?:the )?day after tomorrow', r'tomorrow', r'today', r'tonight', r'in (?:\d+|a|one|two|three|four|five|six|seven|eight|nine|ten) (?:days?|weeks?)',
               r'(?:sometime )?next week', r'this weekend', r'end of the (?:week|month)', r'(?:first|second|third|fourth|last) [a-z]+ (?:of|in) (?:[a-z]+ )?[a-z]+',
               r'\d{1,2}(?:st|nd|rd|th)? (?:of )?' + MONTH_RE, MONTH_RE + r' \d{1,2}(?:st|nd|rd|th)?',
               r'(?:this |next |coming |on )?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)']


def day_from_text(text, now):
    """first readable day phrase, skip after every, each, until"""
    t = (text or '').lower()
    for m in re.finditer(r'\b(' + '|'.join(DAY_PHRASES) + r')\b', t):
        if re.search(r'\b(every|each|until|till|other|second)\s*$', t[:m.start()]):
            continue
        d = words_day(m.group(1), now)
        if d:
            return d
    return None


DETAIL_SLOTS = ('day_words', 'start_words', 'start', 'end', 'minutes', 'who', 'where', 'repeat', 'after_event')


def has_details(e):
    return bool((e.get('day') or {}).get('kind') if isinstance(e.get('day'), dict) else e.get('day')) or \
        any(str(e.get(k) or '').strip() not in ('', '[]') for k in DETAIL_SLOTS)


def drop_non_events(events, ctx):
    """reminders, codes and notes go into notes, duplicate rows merge"""
    if len(events) > 1:
        events = [e for e in events if str(e.get('what') or '').strip() and
                  not re.search(r'^(the )?(day|week|hour) before$', str(e.get('what') or '').strip().lower())] or events[:1]
    if len(events) > 1:
        merged = []
        for e in events:
            w = set(re.findall(r'[a-z]+', str(e.get('what') or '').lower()))
            other = next((m for m in merged if (not has_details(m) or not has_details(e)) and w and
                          (w <= set(re.findall(r'[a-z]+', str(m.get('what') or '').lower())) or
                           set(re.findall(r'[a-z]+', str(m.get('what') or '').lower())) <= w)), None)
            if other is None:
                merged.append(e)
                continue
            longer = max((other.get('what') or '', e.get('what') or ''), key=len)
            for k, v in e.items():
                if k not in ('row', 'what') and str(other.get(k) or '').strip() in ('', '[]', '{}', 'False', 'false'):
                    other[k] = v
            other['what'] = longer
            how(ctx, 'two rows for one thing, merged into "%s"' % longer)
        events = merged
    if len(events) > 1:   # same day, time and place = one event said with and
        sig = lambda e: (json.dumps(e.get('day'), sort_keys=True), str(e.get('day_words') or '').lower(), str(e.get('start_words') or '').lower(),
                         str(e.get('start') or ''), str(e.get('where') or '').lower())
        if len({sig(e) for e in events}) == 1:
            first = dict(events[0])
            first['what'] = ' and '.join(str(e.get('what') or '') for e in events)
            first['who'] = sorted({w for e in events for w in (e.get('who') or [])})
            how(ctx, 'rows with the same day, time and place, merged into "%s"' % first['what'])
            events = [first]
    keep = [e for e in events if not re.search(NOT_EVENT, str(e.get('what') or '').strip().lower())] or events[:1]
    ew = event_words(ctx.get('input') or '')
    if len(keep) > 1 and ew:   # event words cover every row = one event
        said_words = set(re.findall(r'[a-z]+', fix_words(ew).lower()))
        if all(set(re.findall(r'[a-z]+', fix_words(str(e.get('what') or '')).lower())) <= said_words for e in keep):
            first = dict(keep[0])
            for e in keep[1:]:
                for k, v in e.items():
                    if k not in ('row', 'what') and str(first.get(k) or '').strip() in ('', '[]', '{}', 'False', 'false'):
                        first[k] = v
            first['what'] = ew
            how(ctx, 'one event said as "%s", rows merged' % ew)
            keep = [first]
    for e in events:
        if e not in keep:
            k = keep[0]
            k['notes'] = '; '.join(x for x in [str(k.get('notes') or ''), str(e.get('what') or '') + ': ' + str(e.get('notes') or '')] if x.strip())
            how(ctx, '"%s" is not an event, kept in the notes' % e.get('what'))
    return keep


def resolve_when(events, ctx):
    """date, start, end, length, code first, model slots fallback, contradictions ask"""
    now = now_of(ctx)
    existing = read_table('existing-events')
    known = {r['activity'].lower(): int(r['minutes']) for r in read_table('durations') if r.get('minutes', '').isdigit()}
    said = ctx.get('input') or ''
    out = []
    events = drop_non_events([tidy(dict(e)) for e in events or []], ctx)
    one = len(events) == 1   # several events cant share the whole request
    for e in events:
        from_text = one and day_from_text(said, now)
        slot = from_text or words_day(e.get('day_words'), now) or e.get('day')
        if not words_day(e.get('day_words'), now) and not from_text and str(e.get('day_words') or '').strip() and not (e.get('day') or {}).get('kind'):
            ask(ctx, 'Which day is %s? You said "%s".' % (label(e), e.get('day_words')), 'date')
        try:
            e['date'] = resolve_day(slot, now).isoformat()
        except (ValueError, TypeError, KeyError):
            e['date'] = None
            ask(ctx, 'Which day is %s?' % label(e), 'date')
        start = hhmm(said_clock(e.get('start_words'))) or (one and hhmm(said_clock(said))) or None
        start = start or soft_start(e.get('start_words') or '', e.get('what')) or (one and soft_start(said, e.get('what'))) or as_hhmm(e.get('start'))
        after = str(e.get('after_event') or '').strip().lower() or (one and after_event_said(said)) or ''
        if after and e['date']:
            key = re.sub(r'\b(my|the|appointment)\b', '', after).strip()[:5]
            hits = [x for x in existing if key and key in x['title'].lower()]
            hit = next((x for x in hits if x['date'] == e['date']), None)
            if not hit and not (e.get('day') or {}).get('kind') and not words_day(e.get('day_words'), now) and not from_text:
                hit = next((x for x in sorted(hits, key=lambda x: x['date']) if x['date'] >= now.date().isoformat()), None)
                if hit:
                    e['date'] = hit['date']
            clock_said = hhmm(said_clock(said)) if one else hhmm(said_clock(e.get('start_words')))
            if hit and clock_said and clock_said != hit['end']:
                ask(ctx, '%s: after %s (%s), or at %s?' % (label(e), after, hit['end'], clock_said))
            elif hit:
                start = hit['end']
            elif not start:
                ask(ctx, 'Which event is "%s" on %s?' % (after, e['date']), 'start')
        all_day = flag(e, 'all_day') or bool(re.search(r'\b(all|whole) day\b', said, re.I))
        # half day only counts when said as a length
        m = re.search(r'\b(for|keep|the whole|whole|all|the rest of) (the )?(morning|afternoon|evening)\b', said, re.I)
        part = m.group(3).lower() if m else ''
        no_address = re.sub(r'\b\d{1,3}\s+[a-z]+\s+' + STREET, ' ', no_dates(said.lower()))   # house number not a time
        m = re.search(r'\bat (noon|midday|(\d{1,2})(?::(\d{2}))?\s*(am|pm)?)\b', no_address, re.I)
        if m and start and one and (m.group(1).lower() in ('noon', 'midday') or int(m.group(2)) < 24):
            if m.group(1).lower() in ('noon', 'midday'):
                said_start = '12:00'
            else:
                h = int(m.group(2)); ap = (m.group(4) or '').lower()
                h = h + 12 if (ap == 'pm' or (not ap and not m.group(3) and 1 <= h <= 7)) and h < 12 else h
                said_start = '%02d:%s' % (h, m.group(3) or '00')
            if said_start != start:
                ask(ctx, '%s: at %s, or at %s?' % (label(e), said_start, start))
        if one:   # two different times in one request ask
            ranges = re.findall(r'\b(?:between|from)?\s*(\d{1,2}(?::\d{2})?)\s*(?:am|pm)?\s*(?:and|to|-)\s*\d{1,2}(?::\d{2})?\s*(?:am|pm)?', no_dates(said.lower()))
            soft = soft_start(said, e.get('what'))
            clock = hhmm(said_clock(said))
            outside = re.sub(r'\b(?:between|from)?\s*\d{1,2}(?::\d{2})?\s*(?:am|pm)?\s*(?:and|to|-|till|until)\s*\d{1,2}(?::\d{2})?\s*(?:am|pm)?', ' ', no_dates(said.lower()))
            outside = re.sub(r'\b(until|till)\s+\S+(\s*(am|pm))?', ' ', outside)
            single = [hhmm(said_clock(x)) for x in re.findall(r'\b(?:at\s+)?(half \d{1,2}|noon|midday|\d{1,2}:\d{2}|\d{1,2}\s*(?:am|pm))\b', outside)]
            single = [x for x in single if x]
            if ranges and single and hhmm(said_clock(said)) and any(x != hhmm(said_clock(said)) for x in single) and not re.search(r'\b(from|between)\s+(half \d|noon|\d{1,2}:\d{2})', said.lower()):
                ask(ctx, '%s: at %s, or from %s?' % (label(e), [x for x in single if x != hhmm(said_clock(said))][0], hhmm(said_clock(said))))
            elif len(set(ranges)) > 1 or (soft and clock and re.search(r'\b(after lunch|after dinner|after breakfast|in the morning|in the afternoon|in the evening)\b', said, re.I) and soft != clock):
                ask(ctx, '%s: %s?' % (label(e), ' or '.join(sorted(set(ranges))) if len(set(ranges)) > 1 else '%s or %s' % (soft, clock)))
        if all_day and not after and start and re.search(r'\b(between|from)\s+\d{1,2}(?::\d{2})?\s*(am|pm)?\s*(and|to|-)\s*\d{1,2}', no_dates(said.lower())):
            all_day = False   # range beats an all day slip, no question
        if all_day and after:
            ask(ctx, '%s: after %s, or all day?' % (label(e), after))
        elif all_day and (start or part):
            ask(ctx, '%s: all day, or %s?' % (label(e), 'from ' + start if start else 'the ' + part))
        end, window_end = said_end(said) if one else (None, None)
        end = end or (None if one else as_hhmm(e.get('end')))
        if end and start and end <= start and int(end[:2]) < 12 and '%02d%s' % (int(end[:2]) + 12, end[2:]) > start:
            end = '%02d%s' % (int(end[:2]) + 12, end[2:])   # after dinner until 11 = 23:00
        if part in ('morning', 'afternoon', 'evening') and not all_day:
            a, b = setting('half_day_' + part, '08:00-12:00').split('-')
            if (start and not (a <= start < b)) or (end and not (a < end <= b)):
                ask(ctx, '%s: the %s (%s to %s), or from %s?' % (label(e), part, a, b, start or end))
            if not hhmm(said_clock(said)):
                start = a
                clock_now = now_of(ctx)
                if re.search(r'\bthe rest of (the )?' + part, said, re.I) and clock_now.strftime('%Y-%m-%d') == e.get('date') and a <= clock_now.strftime('%H:%M') < b:
                    start = clock_now.strftime('%H:%M')   # rest of the afternoon starts now
            start, end = start or a, end or b
        mins = said_minutes(said) if one else None
        if mins is None and not one:
            try:
                mins = int(float(e.get('minutes'))) if e.get('minutes') not in (None, '') else None
            except (TypeError, ValueError):
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
                if not mins and window_end and window_end > start:
                    end = window_end
                else:
                    if not mins:
                        what = ((event_words(said) if one else None) or e.get('what') or '').lower()
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
    """model rrule checked with dateutil, until from request"""
    from dateutil.rrule import rrulestr
    out = []
    for e in events or []:
        e = dict(e)
        rule = re.sub(r'^RRULE:', '', str(e.get('repeat') or '').strip(), flags=re.I).strip(' ;').upper()
        rule = rule.replace('FREQ=BIWEEKLY', 'FREQ=WEEKLY;INTERVAL=2')
        if e.get('date'):
            words_rule = said_repeat(ctx.get('input') or '', date.fromisoformat(e['date']))
            if words_rule:
                rule = words_rule
        rule = ';'.join(p for p in rule.split(';') if p and not p.startswith('UNTIL='))
        # model writes tue or tuesday, rfc 5545 wants tu
        rule = re.sub(r'BYDAY=([^;]*)', lambda b: 'BYDAY=' + re.sub(r'([+-]?\d*)(MON|TUE|WED|THU|FRI|SAT|SUN)[A-Z]*', lambda m: m.group(1) + m.group(2)[:2], b.group(1)), rule)
        words = str(e.get('until_words') or '').strip()
        m = re.search(r'\b(?:until|till)\s+((?:the\s+)?(?:end of (?:the )?\w+|\d{4}-\d{2}-\d{2}|\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}(?:st|nd|rd|th)?|christmas(?: day)?|new year(?:\'s eve)?|easter))\b',
                      re.sub(r'"[^"]*"', '', (ctx.get('input') or '').lower()))
        words = m.group(1) if m else ''   # repeat end from request, never guessed
        if 'COUNT=' in rule or re.match(r'^(for\s+)?\d+\s+(weeks?|days?|months?|times)$', words.lower()):
            words = ''   # for 4 weeks is a count not an end date
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

EVENT_STOP = r'(?:with|w/|w|for|at|on|in|from|every|each|this|next|coming|tomorrow|today|tonight|after|until|till|between|sometime|end of (?:the )?(?:week|month)|here|via|by|once|weekly|daily|monthly|the (?:first|second|third|fourth|last)|\d)'


def vocabulary():
    """alex event words from the tables"""
    words = set()
    for r in read_table('durations'):
        words.update(r['activity'].lower().split())
    for r in read_table('calendars'):
        for k in split_list(r['keywords']):
            words.update(k.split())
    words.update(w for w in split_list(setting('vocabulary')) for w in w.split())
    for r in read_table('abbreviations'):
        words.update(r['long'].lower().split())
    return {w for w in words if len(w) >= 3}


def fix_words(text):
    """speech fixes, abbreviations, then typos near alex vocab"""
    for r in read_table('speech-fixes'):
        text = re.sub(r'(?i)\b' + re.escape(r['heard']) + r'\b', r['meant'], text)
    for r in read_table('abbreviations'):
        if r['short'].isalnum() or r['short'] in ('w/',):
            text = re.sub(r'(?i)(?<![\w/])' + re.escape(r['short']) + r'(?![\w/])', r['long'], text)
        elif r['short'] in ('+', '&'):
            text = text.replace(' %s ' % r['short'], ' %s ' % r['long'])
    vocab = vocabulary()
    out = []
    for w in text.split(' '):
        low = w.lower().strip('.,')
        if len(low) == 3 and low.isalpha() and low not in vocab:
            m = [v for v in vocab if len(v) == 3 and sorted(v) == sorted(low)]   # gmy = gym
            if m:
                w = m[0]
        elif len(low) >= 4 and low.isalpha() and low not in vocab:
            m = difflib.get_close_matches(low, vocab, n=1, cutoff=0.8)
            if m and sorted(m[0]) == sorted(low) or (m and difflib.SequenceMatcher(None, low, m[0]).ratio() >= 0.86):
                w = m[0]
        out.append(w)
    return ' '.join(out)


def event_words(said):
    """event words from request, none if nothing left"""
    t = event_sentence(said)
    t = re.sub(r'(?i)^(tomorrow|today|tonight)\s+at\s+\S+(\s+(am|pm))?\s+', '', t)
    t = re.sub(r'(?i)^(please\s+|can you\s+|could you\s+)?(schedule|set up|add|put|create|book|make|block)\s+', '', t)
    t = re.sub(r'(?i)\b(uhh*|umm*|finally|quick|just|maybe|possibly)\b,?\s*', '', t)
    t = re.sub(r'(?i)\b(?:that|the)\s+([a-z][a-z ]*?)\s+thing\b', r'\1', t)   # that lunch thing = lunch
    t = re.sub(r'(?i),?\s*the [a-z ]+? one\b|,?\s*or whatever\b', '', t)
    t = re.sub(r'(?i)^(a|an|the|my)\s+', '', t.strip(' ,.'))
    t = fix_words(t)
    t = t.split(',')[0]   # aside after comma not the event
    m = re.search(r'(?i)(?:\s|\.|$)' + EVENT_STOP + r'\b', ' ' + t + ' ')
    words = t[:max(m.start() - 1, 0)].strip(' ,.') if m else t.strip(' ,.')
    words = re.sub(r'(?i)^(a|an|the|my)\s+', '', words)
    return words or None


def named_people(people, said, what=''):
    """people the title names, once each, only if request names them"""
    table = read_table('people')
    places = {n for r in read_table('places') for n in [r['place'].lower()] + split_list(r.get('also_written_as'))}
    low = said.lower()
    out = []
    for p in people:
        p = str(p).strip()
        as_said = re.sub(r'\s*\(.*\)$', '', p).strip().lower()
        p = SLIPS.get(as_said, p)   # misheard names
        base = re.sub(r'\s*\(.*\)$', '', p).strip().lower()
        if not base or base in places:
            continue
        rows = [r for r in table if p.lower() in (r['person'].lower(), r['email'].lower())] or \
               [r for r in table if base == re.sub(r'\s*\(.*\)$', '', r['person']).lower() or base in split_list(r.get('also_written_as'))]
        spell = [base, as_said] + [n for r in rows for n in [re.sub(r'\s*\(.*\)$', '', r['person']).lower(), r['email'].lower()] + split_list(r.get('also_written_as'))]
        if not any(re.search(r'\b' + re.escape(n) + r"\b(?!'s)", low) for n in spell if n):
            continue
        name = re.sub(r'\s*\(.*\)$', '', rows[0]['person']) if rows else ('' if '@' in p else p)
        if name and not re.search(r'\b' + re.escape(re.sub(r'^the ', '', name.lower())) + r"\b(?!'s)", what.lower()) and name not in out:
            out.append(name)
    return out


def make_title(events, ctx):
    """title from what + who by naming rules"""
    pats = {r['rule']: r['pattern'] for r in read_table('naming')}
    companies = read_table('companies')
    out = []
    for e in events or []:
        e = dict(e)
        what = (event_words(ctx.get('input') or '') if len(events or []) == 1 else None) or (e.get('what') or '').strip()
        if '?' in str(e.get('ask') or ''):   # no question mark = not a real question
            ask(ctx, str(e['ask']).strip(), 'what')
        what = re.sub(r'\b(the thing|or whatever|finally|quick|uh+|um+)\b,?', ' ', what, flags=re.I)
        what = re.sub(r'\s+', ' ', what).strip(' ,.')
        what = re.sub(r'^((please|can you|could you|schedule|set up|add|put|create|book|make|block|the|a)\s+)+', '', what, flags=re.I).strip(' ,.')
        for r in read_table('abbreviations'):
            if r['short'].isalnum() or r['short'] in ('w/',):
                what = re.sub(r'(?i)(?<![\w/])' + re.escape(r['short']) + r'(?![\w/])', r['long'], what)
        what, _, inside = what.partition(' with ')   # people after with go to the people part
        what = re.sub(r'(?i)\s+from [a-z]+ to [a-z]+$', '', what).strip(' ,.')
        what = what[:1].upper() + what[1:]
        first = clean_sentence(ctx.get('input') or '')
        if re.search(r'(?i)\b(maybe|possibly|pencil in)\b', first):
            e['maybe'] = True
        if re.search(r'(?i)\bnot (agreed|confirmed)( yet)?\b', first):
            e['not_agreed'] = True
        if not what:
            ask(ctx, 'What is the event?', 'what')
        elif re.search(PROMPT_WORDS, what, re.I):
            ask(ctx, 'What should "%s" be called?' % what, 'title')
        said_names = [x['person'] for x in said_people(event_sentence(ctx.get('input') or ''))]   # event sentence only, a name in a note isnt coming
        who = named_people(said_names + [x.strip() for x in re.split(r' and |,', inside) if x.strip()] + list(e.get('people') or []) + list(e.get('who') or []), event_sentence(ctx.get('input') or ''), what)
        home = re.search(r"\b(\w+)'s\b", first)
        if not who and home:   # dinner at mum's = dinner with mum
            fam = next((r for r in read_table('people') if r['calendar'] == 'Family' and home.group(1).lower() in
                        [re.sub(r'\s*\(.*\)$', '', r['person']).lower()] + split_list(r.get('also_written_as'))), None)
            if fam:
                who = [re.sub(r'\s*\(.*\)$', '', fam['person'])]
        title = pats['with_who'].replace('{What}', what).replace('{who}', ' and '.join(who)) if who else pats['plain'].replace('{What}', what)
        text = (what + ' ' + ' '.join(who) + ' ' + first).lower()
        co = next((c for c in companies if any(re.search(r'\b' + re.escape(k) + r'\b', text) for k in split_list(c['keywords']))), None)
        if not co:   # person's company from people table
            named = said_people(ctx.get('input') or '')
            cal = code_calendar(e, ctx.get('input') or '', named) or str(e.get('calendar') or '')
            firsts = {}
            for r in named:   # same first name, pick by calendar
                firsts.setdefault(re.sub(r'\s*\(.*\)$', '', r['person']).lower(), []).append(r)
            chosen = [g[0] if len(g) == 1 else (next((r for r in g if r['calendar'] == cal), None) or next((r for r in g if r['calendar'] != 'Work'), g[0]))
                      for g in firsts.values()]
            shorts = {r['company'] for r in chosen if r.get('company')}
            pick = 'FAB' if 'FAB' in shorts else ('NW' if 'NW' in shorts else None)
            co = next((c for c in companies if c['short'] == pick), None) if pick else None
        if co:   # company in brackets, no temp or ph
            title = pats['company'].replace('{SHORT}', co['short']).replace('{title}', title)
        if flag(e, 'maybe'):
            title = pats['maybe'].replace('{title}', title)
        how(ctx, title)
        out.append(dict(e, title=title))
    return done(ctx, out)


# ---------- (e)(f) calendar, people, clash ----------

# ---------- lookups, code first, model fallback ----------

JOURNEY = r'\b(train|trn|flight|fly|drive|bus|commute|trip|travel|ride to|journey)\b'


def said_people(said):
    """people the request names, mum's is a place"""
    t = re.sub(r'"[^"]*"', '', said.lower())
    for word, person in [(w, SLIPS.get(w)) for w in set(re.findall(r'[a-z]+', t))]:
        if person and person != 'the client' and word not in ('client',):
            t += ' ' + person.lower()   # misheard name counts
    out = []
    for r in read_table('people'):
        names = [re.sub(r'\s*\(.*\)$', '', r['person']).lower()] + split_list(r.get('also_written_as'))
        names = [n for n in names if n not in ('team', 'client', 'boss')] + (['boss'] if r['person'] == 'Martin' else [])
        if r['person'] in ('the team', 'the client'):
            word = r['person'].split()[-1]
            if re.search(r'\b(with|w/|w|and|invite|send)\s+(the\s+)?' + word + r'\b', t) or \
                    re.search(r'\bthe ' + word + r'\s+(is|are|joins|joining|dials|will|comes|coming)\b', t) or \
                    (word == 'client' and re.search(r'\bfabrikam\b', t)):
                out.append(r)
            continue
        if any(re.search(r'\b' + re.escape(n) + r"\b(?!'s)", t) for n in names if n) or r['email'].lower() in t:
            out.append(r)
    return out


def event_sentence(said):
    """first sentence, quotes out"""
    t = re.sub(r'"[^"]*"', '', said)
    return re.split(r'(?<=[.!?])\s+(?=[A-Z])', t.strip())[0]


def clean_sentence(said):
    """event sentence fixed, after my x removed so earlier event never decides calendar"""
    t = event_sentence(said)
    for r in read_table('speech-fixes'):
        t = re.sub(r'(?i)\b' + re.escape(r['heard']) + r'\b', r['meant'], t)
    return re.sub(r'(?i)\bafter (my|the) [a-z ]+?(?=\s+(for|at|on|in|until|till|from|with|to|every|and)\b|[,.]|$)', ' ', t)


def code_calendar(e, said, named):
    """calendar by owner rules, none = model pick"""
    cals = read_table('calendars')
    first = clean_sentence(said).lower()
    what = str(e.get('what') or '').lower()
    person_words = {n for p in read_table('people') for n in [re.sub(r'\s*\(.*\)$', '', p['person']).lower()] + split_list(p.get('also_written_as'))}
    if re.search(JOURNEY, what + ' ' + first) and ('->' in str(e.get('where') or '') or re.search(r'\bfrom \w+ to \w+', first)) \
            and not any(re.search(r'\b' + re.escape(k) + r'\b', what + ' ' + first) for c in cals if c['use'] != 'travel' for k in split_list(c['keywords']) if k not in person_words | {'martin', 'client', 'team'}):
        return next((c['calendar'] for c in cals if c['use'] == 'travel'), None)
    if any(p['person'] in ('Martin', 'the client') for p in named) or re.search(r'\b(client|klein|fabrikam)\b', first):
        return 'Work'
    person_words = {n for p in read_table('people') for n in [re.sub(r'\s*\(.*\)$', '', p['person']).lower()] + split_list(p.get('also_written_as'))}
    hits = sorted(((len(k), c['calendar']) for c in cals for k in split_list(c['keywords'])
                   if k not in person_words and re.search(r'\b' + re.escape(k) + r'\b', what + ' ' + first)), reverse=True)
    here = {p['person'] for p in said_people(event_sentence(said))}
    if hits and hits[0][1] == 'Self-Improvement' and any(p['calendar'] == 'Friends' and p['person'] in here for p in named):
        return 'Friends'   # sport with friends goes on friends
    if hits:
        return hits[0][1]
    own = {p['calendar'] for p in named if p['person'] in here and p['person'] not in ('Jordan (work)',)}
    if len(own) == 1:
        return own.pop()
    if re.search(r"\b(mum|mama|mom|dad|grandma)'s\b", first):   # family member's home, nobody else named
        return 'Family'
    return None


def code_place(said, e):
    """place from event sentence"""
    first = event_sentence(said)
    m = re.search(r'https?://\S+', first)
    if m:
        return m.group(0).rstrip('.,')
    if re.search(r'\bon teams\b', first, re.I):
        return 'Microsoft Teams'
    m = re.search(r'\bfrom ([A-Z][a-z]+) to ([A-Z][a-z]+)\b', first)
    if m:
        return '%s -> %s' % m.groups()
    m = re.search(r'\b(?:at|in)\s+(\d{1,3}\s+[A-Z][a-z]+\s+' + STREET.replace(r'\b', '') + r')', first, re.I)
    if m:
        return m.group(1)
    m = re.search(r'\bin (room [\w.]+)', first, re.I)
    if m:
        return m.group(1)
    low = first.lower()
    for r in sorted(read_table('places'), key=lambda r: -len(r['place'])):
        for n in sorted([r['place'].lower()] + split_list(r.get('also_written_as')), key=len, reverse=True):
            n2 = re.sub(r'^(at|on|the)\s+', '', n)
            if re.search(r'\b(?:at|in|to|meet at)\s+(?:the\s+)?' + re.escape(n2) + r'\b', low):
                return r['place']
    m = re.search(r'\bin ([A-Z][a-z]{2,})\b', first)
    if m and m.group(1) not in ('December', 'November', 'January', 'October'):
        return m.group(1)
    return None


def said_place(place, said):
    """place in request by any spelling"""
    low = event_sentence(said).lower()   # never a place from a note
    names = [place.lower()] + [n for r in read_table('places') if r['place'].lower() == place.lower() for n in split_list(r.get('also_written_as'))]
    parts = [x.strip() for x in re.split(r'->', place.lower())]
    return any(re.sub(r'^(the|at|on)\s+', '', n) in low for n in names if n) or all(x in low for x in parts if x)


def code_notes(said):
    """notes = sentences after event sentence minus reminder, plus quote"""
    parts = re.split(r'(?<=[.!?])\s+(?=[A-Z])', said.strip())
    notes = [p for p in parts[1:] if not re.match(r'^(remind me|reminder)\b', p, re.I)]
    quote = re.findall(r'"([^"]+)"', parts[0]) if parts else []
    text = ' '.join(quote + notes).strip()
    return text or None


class _Slips(dict):
    """misheard single words that mean a person"""
    def get(self, k, default=None):
        people = {n for r in read_table('people') for n in [r['person'].lower()] + split_list(r.get('also_written_as'))}
        companies = {r['company'].lower() for r in read_table('companies')}
        if any(c in k for c in companies) or k == 'client':
            return 'the client'   # known company = the client
        for r in read_table('speech-fixes'):   # whole table entries only, unseen slips stay unseen for test set
            if r['heard'].lower() == k and r['meant'].lower() in people:
                return r['meant']
        return default


SLIPS = _Slips()


def check_event(events, ctx):
    """calendar, emails, clashes, confirm first"""
    cals = [r['calendar'] for r in read_table('calendars')]
    people = {r['person'].lower(): r for r in read_table('people')}
    existing = read_table('existing-events')
    anyway = re.search(r'book (it )?anyway|anyway', ctx.get('input') or '', re.I)
    out = []
    said_raw = ctx.get('input') or ''
    named = said_people(said_raw)
    for e in events or []:
        e = dict(e)
        c = code_calendar(e, said_raw, named) or str(e.get('calendar') or '').strip()
        if c not in cals:
            opts = [x.strip() for x in re.split(r'\s+or\s+|,', c) if x.strip() in cals] or difflib.get_close_matches(c, cals, n=2, cutoff=0.5)
            ask(ctx, 'Which calendar is %s on? %s' % (label(e), ' or '.join(opts) if len(opts) > 1 else (opts[0] + '?' if opts else '')), 'calendar')
            c = opts[0] if len(opts) == 1 else None
        e['calendar'] = c
        att = []
        place_names = {n for r in read_table('places') for n in [r['place'].lower()] + split_list(r.get('also_written_as'))}
        said = (ctx.get('input') or '').lower()
        said_emails = [m.rstrip('.') for m in re.findall(r'[\w.+-]+@[\w-]+\.[\w.]+', said)]
        for p in [x['person'] for x in named] + list(e.get('people') or []) + list(e.get('who') or []) + said_emails:
            if re.search(r'\(place\)', str(p), re.I) or re.search(r'\b(from|to)\s+' + re.escape(str(p).strip()) + r'\b', said_raw, re.I):
                continue   # model put a place among the people
            as_said = re.sub(r'\s*\(.*\)$', '', str(p)).strip().lower()
            p = SLIPS.get(str(p).strip().lower(), p)
            p = SLIPS.get(re.sub(r'\s*\(.*\)$', '', str(p)).strip().lower(), p)
            base = re.sub(r'\s*\(.*\)$', '', str(p)).strip().lower()
            row = people.get(str(p).strip().lower()) or people.get(base)
            names = [base, as_said] + (split_list(row.get('also_written_as')) if row else [])
            if not str(p).strip() or base in place_names or str(p).strip() in cals:
                continue
            if '@' not in base and not any(re.search(r'\b' + re.escape(n) + r'\b', said) for n in names if n):
                continue
            if base in ('client', 'the client', 'team', 'the team') and not any(x['person'] == 'the ' + base.replace('the ', '') for x in named):
                continue   # client workshop names the event, not the client coming
            r = people.get(str(p).strip().lower())
            if not r:
                base = re.sub(r'\s*\(.*\)$', '', str(p)).strip().lower()
                spelled = [x for x in people.values() if base in split_list(x.get('also_written_as'))]
                r = people.get(base) or (spelled[0] if len(spelled) == 1 else None)
            if not r:
                lp = str(p).strip().lower()
                hits = [x for x in people.values() if lp in split_list(x.get('also_written_as')) or lp == re.sub(r'\s*\(.*\)$', '', x['person']).lower()]
                if len(hits) > 1:   # same first name, this calendar else not work
                    hits = [x for x in hits if x['calendar'] == c] or [x for x in hits if x['calendar'] != 'Work']
                r = hits[0] if len(hits) == 1 else None
            if r:
                first = re.sub(r'\s*\(.*\)$', '', r['person']).lower()
                twins = [x for x in people.values() if re.sub(r'\s*\(.*\)$', '', x['person']).lower() == first]
                if len(twins) > 1:
                    r = next((x for x in twins if x['calendar'] == c), None) or next((x for x in twins if x['calendar'] != 'Work'), r)
                if invited(r, said):
                    att.append({'name': re.sub(r'\s*\(.*\)$', '', r['person']), 'email': r['email']})
            elif '@' in str(p):
                att.append({'name': p, 'email': p})
            elif re.search(r'\b' + re.escape(str(p).strip()[:1].upper() + str(p).strip()[1:]) + r'\b', said_raw):
                ask(ctx, 'Who is %s?' % p, 'attendees')   # only capitalised names, never braces
        e['attendees'] = list({a['email'].lower(): a for a in att}.values())   # one invite per email
        if e.get('date') and e.get('start') and not anyway:
            for x in existing:
                if x['date'] == e['date'] and x['start'] < (e['end'] if e.get('end_date') == e['date'] else '24:00') and e['start'] < x['end']:
                    ask(ctx, '%s clashes with %s (%s to %s). Book anyway?' % (label(e), x['title'], x['start'], x['end']))
        if flag(e, 'confirm_first'):
            ask(ctx, 'Shall I put in %s on %s?' % (label(e), e.get('date')))
        how(ctx, '%s, %s' % (c or '?', ', '.join(a['name'] for a in att) or 'nobody'))
        keep = ['row', 'title', 'date', 'end_date', 'start', 'end', 'allDay', 'location', 'attendees', 'calendar', 'rrule', 'desc', 'reminder_minutes']
        guess = place_of(e.get('where'))
        if guess and not said_place(guess, said_raw):
            guess = None   # never invent a place
        e['location'] = code_place(said_raw, e) or guess
        e['desc'] = code_notes(said_raw) or e.get('desc')
        e['reminder_minutes'] = said_reminder(ctx.get('input'))
        out.append({k: e.get(k) for k in keep})
    return done(ctx, out)


def invited(person, said):
    """invite only if table says yes, request asks, or email written"""
    if str(person.get('invite') or '').strip().lower() == 'yes' or person['email'].lower() in said:
        return True
    names = [re.sub(r'\s*\(.*\)$', '', person['person']).lower()] + split_list(person.get('also_written_as'))
    return any(re.search(r'\b(invite|send)\b[^.]{0,30}\b' + re.escape(n) + r'\b|\b' + re.escape(n) + r'\b[^.]{0,15}\binvite\b', said)
               for n in names if n)


def place_of(where):
    """canonical place from places.csv, else as said"""
    w = str(where or '').split(';')[0].strip()
    if not w:
        return None
    m = re.match(r'^(?:from\s+)?([A-Z][\w\'. ]*?)\s+to\s+([A-Z][\w\'. ]*)$', w)
    if m:   # a to b written a -> b
        return '%s -> %s' % (m.group(1).strip(), m.group(2).strip())
    if '->' in w or re.search(r'https?://', w):
        return w
    low = re.sub(r'^(the|at|in|on)\s+', '', w.lower())
    for r in read_table('places'):
        names = [r['place'].lower()] + split_list(r.get('also_written_as'))
        if low in [re.sub(r'^(the|at|in|on)\s+', '', n) for n in names]:
            return r['place']
    return w


# ---------- (g) write guard ----------

def test_send(rows, ctx):
    """every event to the test calendar, people in notes never invites"""
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


def join_title(events, ctx):
    """v3, only joins model parts by naming pattern, no decisions"""
    pats = {r['rule']: r['pattern'] for r in read_table('naming')}
    out = []
    for e in events or []:
        e = dict(e)
        what = str(e.get('what') or '').strip()
        what = what[:1].upper() + what[1:]
        people = [str(p).strip() for p in (e.get('people') if isinstance(e.get('people'), list) else []) if str(p).strip()]
        people = [p for p in people if p.lower() not in what.lower()]
        title = pats['with_who'].replace('{What}', what).replace('{who}', ' and '.join(people)) if people else pats['plain'].replace('{What}', what)
        co = str(e.get('company') or '').strip().strip('()')
        if co:
            title = pats['company'].replace('{SHORT}', co).replace('{title}', title)
        if str(e.get('maybe')).lower() == 'true':
            title = pats['maybe'].replace('{title}', title)
        out.append(dict(e, title=title))
    return out


STEPS = {'thesis_join_title': join_title, 'thesis_when': resolve_when, 'thesis_repeat': resolve_repeat, 'thesis_title': make_title,
         'thesis_check': check_event, 'thesis_test_send': test_send}
