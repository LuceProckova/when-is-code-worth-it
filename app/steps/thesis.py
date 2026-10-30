"""Code steps for the thesis test flow (create events, code condition). Same idea as calendar.py, but it reads
the frozen test tables in My Data/knowledge/thesis, takes "today" from the test row, never asks mid-run
(an ask is written into the row and the run goes on), and every event lands in one calendar for the test.

  calendars.csv      calendar, keywords, use
  people.csv         person, also_written_as, email, calendar
  places.csv         place, also_written_as, address
  durations.csv      activity, minutes
  existing-events.csv  title, date, start, end, calendar
  naming.csv         rule, pattern
  speech-fixes.csv   heard, meant
  abbreviations.csv  short, long
  settings.csv       setting, value
"""
import csv
import difflib
import os
import re
from datetime import date, datetime, timedelta

KNOW = None   # set by papertrail: My Data/knowledge
AREA = 'thesis'
DAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
BYDAY = ['MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU']
MONTHS = ['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september',
          'october', 'november', 'december']


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


def today_of(ctx):
    """The test row's today, else the computer's."""
    t = ctx.get('test_today')
    return datetime.strptime(t, '%Y-%m-%d %H:%M') if t else datetime.now()


def how(ctx, text):
    """How a step got its answer, one line per event, shown under the answer."""
    ctx.setdefault('_how', []).append(text)


def done(ctx, out):
    ctx['_note'] = ' | '.join(x for x in ctx.pop('_how', []) if x)
    return out


def ask(ctx, text, field=None):
    if field:
        ctx.setdefault('asked_fields', set()).add(field)
    ctx.setdefault('asks', [])
    if ctx['asks'] is None:
        ctx['asks'] = []
    if text not in ctx['asks']:
        ctx['asks'].append(text)


# ---------- title ----------

def clean_words(text, why=None):
    """Speech-to-text slips from speech-fixes.csv, abbreviations from abbreviations.csv, typos against
    the words the tables know. why collects each change and the table it came from."""
    why = why if why is not None else []
    t = ' ' + (text or '').strip() + ' '
    for r in sorted(read_table('speech-fixes'), key=lambda r: -len(r['heard'])):
        if re.search(r'(?<![\w])' + re.escape(r['heard']) + r'(?![\w])', t, re.I):
            why.append('"%s" is "%s" (speech fixes)' % (r['heard'], r['meant']))
        t = re.sub(r'(?<![\w])' + re.escape(r['heard']) + r'(?![\w])', r['meant'], t, flags=re.I)
    ab = {r['short'].lower(): r['long'] for r in read_table('abbreviations')}
    vocab = set()
    for r in read_table('durations'):
        vocab.update(r['activity'].lower().split())
    for r in read_table('calendars'):
        for k in split_list(r.get('keywords')):
            vocab.update(k.split())
    vocab.update(w.lower() for w in re.findall(r'[A-Za-z]+', setting('vocabulary').replace(';', ' ')))
    out = []
    for w in t.split():
        core = w.lower().strip('.,')
        if core in ab:
            out.append(ab[core])
            why.append('"%s" is "%s" (abbreviations)' % (core, ab[core]))
        elif len(core) > 3 and core.isalpha() and core not in vocab:
            m = difflib.get_close_matches(core, vocab, n=1, cutoff=0.8)
            fixed = m[0] if m and sorted(m[0]) == sorted(core) or (m and abs(len(m[0]) - len(core)) <= 1) else w
            if fixed != w:
                why.append('"%s" is a typo of "%s"' % (core, fixed))
            out.append(fixed)
        else:
            out.append(w)
    return ' '.join(out).strip()


def make_title(events, ctx):
    """The title from the model's cleaned words, built by the rules in naming.csv. The model only says what it is,
    who it is with and whether it is a maybe, not agreed or booked without an invite. Code puts it together."""
    pats = {r['rule']: r['pattern'] for r in read_table('naming')}
    companies = read_table('companies')
    names = {}
    for r in read_table('people'):
        for k in [r['person'], r['email']] + split_list(r.get('also_written_as')):
            names[k.lower()] = re.sub(r'\s*\(.*\)$', '', r['person'])
    out = []
    for e in events or []:
        e = dict(e)
        why = []
        what = (e.get('what') or '').strip()
        # the model may ask only when it cannot tell what the event is, without that nothing else can be filled in
        if str(e.get('ask') or '').strip() or not what:
            ask(ctx, str(e.get('ask') or '').strip() or 'What is the event?', 'what')
        # leftover command words and fillers are not part of the name
        what = re.sub(r'^((please|can you|could you|schedule|set up|add|put|create|book|make|block)\s+)+', '', what, flags=re.I)
        what = re.sub(r'^((uhh?|um+|that|the|a)\s+)+', '', what, flags=re.I).strip(' ,')
        what = what[:1].upper() + what[1:]
        # with who: the people's names, once, unless the name is already in what
        who = [names.get(w.strip().lower(), w.strip()) for w in (e.get('who') or []) if w.strip()]
        who = [w for w in who if w.lower() not in what.lower() and w.lower() not in ('my boss', 'the team', 'the client')]
        title = pats['with_who'].replace('{What}', what).replace('{who}', ' and '.join(who)) if who else pats['plain'].replace('{What}', what)
        if who:
            why.append('with %s (naming)' % ' and '.join(who))
        # work for a company: its short name in front, from companies.csv
        text = (what + ' ' + ' '.join(e.get('who') or [])).lower()
        co = next((c for c in companies if any(re.search(r'\b' + re.escape(k) + r'\b', text) for k in split_list(c['keywords']))), None)
        flag = lambda k: str(e.get(k)).lower() == 'true'
        if co and flag('not_agreed'):
            title = pats['not_agreed'].replace('{SHORT}', co['short']).replace('{title}', title)
            why.append('%s, not agreed yet (naming, companies)' % co['company'])
        elif co and flag('booked_no_invite'):
            title = pats['booked_no_invite'].replace('{SHORT}', co['short']).replace('{title}', title)
            why.append('%s, booked but no invite yet (naming, companies)' % co['company'])
        elif co:
            title = pats['company'].replace('{SHORT}', co['short']).replace('{title}', title)
            why.append('work for %s (companies)' % co['company'])
        if flag('maybe') and not title.lower().startswith('maybe') and not (co and (flag('not_agreed') or flag('booked_no_invite'))):
            title = pats['maybe'].replace('{title}', title)
            why.append('you said maybe (naming)')
        how(ctx, '; '.join(why))
        out.append(dict(e, title=title))
    return done(ctx, out)


# ---------- day and time ----------

def resolve_day(phrase, today, now=None):
    """The day words as a date. now is the time of the request, so tomorrow said after midnight is still today."""
    p = re.sub(r'^(on|the)\s+', '', (phrase or '').strip().lower())
    if not p or p in ('today', 'this evening', 'this afternoon', 'tonight', 'this morning'):
        return today
    if p == 'tomorrow':
        # said before tomorrow_means_today_before (settings.csv) she has not slept yet, so tomorrow is today
        late = now and now.strftime('%H:%M') < setting('tomorrow_means_today_before', '00:00')
        return today if late else today + timedelta(days=1)
    if p in ('day after tomorrow', 'the day after tomorrow'):
        return today + timedelta(days=2)
    m = re.match(r'^in (\d+) (day|days|week|weeks)$', p)
    if m:
        return today + timedelta(days=int(m.group(1)) * (7 if m.group(2).startswith('w') else 1))
    m = re.match(r'^(\d{4})-(\d{1,2})-(\d{1,2})$', p)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.match(r'^(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?([a-z]+)$', p) or re.match(r'^([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?$', p)
    if m:
        a, b = m.groups()
        d, mon = (a, b) if a.isdigit() else (b, a)
        mi = next((i for i, n in enumerate(MONTHS) if n.startswith(mon[:3])), None)
        if mi is not None:
            x = date(today.year, mi + 1, int(d))
            return x if x >= today else date(today.year + 1, mi + 1, int(d))
    m = re.match(r'^(first|second|third|fourth|last) ([a-z]+) (?:of|in) (?:the |each |every )?([a-z ]+)$', p)
    if m:
        wd = next(i for i, n in enumerate(DAYS) if n.startswith(m.group(2)[:3]))
        mon = m.group(3).strip()
        if mon in ('next month', 'next'):
            y, mo = (today.year + (today.month == 12), today.month % 12 + 1)
        elif mon == 'month':
            y, mo = today.year, today.month
        else:
            mo = next(i for i, n in enumerate(MONTHS) if n.startswith(mon[:3])) + 1
            y = today.year if mo >= today.month else today.year + 1
        pick = lambda y, mo: [date(y, mo, d) for d in range(1, 32) if _valid(y, mo, d) and date(y, mo, d).weekday() == wd]
        nth = lambda ds: ds[-1] if m.group(1) == 'last' else ds[['first', 'second', 'third', 'fourth'].index(m.group(1))]
        x = nth(pick(y, mo))
        if x < today:   # already gone this month or this year, so the next one
            y, mo = (y + 1, mo) if mon not in ('month', 'next month', 'next') else (y + (mo == 12), mo % 12 + 1)
            x = nth(pick(y, mo))
        return x
    if p in ('end of the week', 'the end of the week'):
        return today + timedelta(days=(4 - today.weekday()) % 7)
    if p in ('end of the month', 'the end of the month'):
        nxt = date(today.year + (today.month == 12), today.month % 12 + 1, 1)
        return nxt - timedelta(days=1)
    if p in ('next week', 'sometime next week'):
        return today + timedelta(days=7 - today.weekday())
    if p in ('this weekend', 'weekend'):
        return today + timedelta(days=(5 - today.weekday()) % 7)
    m = re.match(r'^(this |next )?([a-z]+)$', p)
    if m:
        wd = next((i for i, n in enumerate(DAYS) if n.startswith(m.group(2)[:3])), None)
        if wd is not None:
            ahead = (wd - today.weekday()) % 7
            if m.group(1) == 'next ':
                ahead = ahead + 7 if ahead == 0 or setting('next_means') == 'week_after' else ahead
            return today + timedelta(days=ahead)
    raise ValueError('cannot read the day "%s"' % phrase)


def _valid(y, m, d):
    try:
        date(y, m, d)
        return True
    except ValueError:
        return False


def clock(t, pm_guess=True):
    """'3 pm', '15:00', 'half 3', 'noon', '7:30' as HH:MM. Bare small hours are afternoon (1 to 7)."""
    t = (t or '').strip().lower().replace('a.m.', 'am').replace('p.m.', 'pm').replace('.', ':')
    t = re.sub(r"\s*o'?clock$", '', t)
    if t in ('noon', 'midday', '12 noon'):
        return setting('noon', '12:00')
    if t in ('midnight',):
        return '00:00'
    # british spoken times: half 3 and half past 3 are 3:30, quarter past and quarter to
    m = re.match(r'^(half|quarter)(?: (past|to))? (\d{1,2})\s*(am|pm)?$', t)
    if m:
        h = int(m.group(3))
        mi = 30 if m.group(1) == 'half' else (45 if m.group(2) == 'to' else 15)
        h = h - 1 if m.group(2) == 'to' else h
        h = h + 12 if (m.group(4) == 'pm' or (not m.group(4) and pm_guess)) and 1 <= h <= 7 + 4 * (m.group(4) == 'pm') else h
        return '%02d:%02d' % (h, mi)
    m = re.match(r'^(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?$', t.replace(' ', '') if 'm' not in t else t)
    if not m:
        m = re.match(r'^(\d{1,2})(?::(\d{2}))?\s*(am|pm)$', t)
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or '').replace('.', '')
    if ap == 'pm' and h < 12:
        h += 12
    if ap == 'am' and h == 12:
        h = 0
    if not ap and pm_guess and 1 <= h <= 7:
        h += 12
    return '%02d:%02d' % (h, mi) if h < 24 else None


SOFT = {'in the morning': 'morning_start', 'morning': 'morning_start', 'in the afternoon': 'afternoon_start', 'afternoon': 'afternoon_start',
        'in the evening': 'evening_start', 'evening': 'evening_start', 'this evening': 'evening_start', 'this afternoon': 'afternoon_start',
        'early': 'early_start', 'late morning': 'late_morning_start', 'after lunch': 'after_lunch_start', 'at lunchtime': 'lunchtime',
        'lunchtime': 'lunchtime', 'after dinner': 'after_dinner_start'}


def resolve_when(events, ctx):
    """Day from the words and the test row's today, start from a clock time, a soft time (settings.csv) or the end of
    the event it comes after (existing-events.csv)."""
    today = today_of(ctx).date()
    out = []
    for e in events or []:
        e = dict(e)
        try:
            e['date'] = resolve_day(e.get('day'), today, today_of(ctx)).isoformat()
        except ValueError:
            e['date'] = today.isoformat()
            ask(ctx, 'Which day is %s?' % (e.get('title') or e.get('what')))
        s = (e.get('start') or '').strip().lower()
        s = re.sub(r'^(at|around|from)\s+', '', s)
        win = re.match(r'^(?:sometime )?between (.+?) and (.+)$', s)
        aft = re.match(r'^after (?:my |the )?(.+)$', s)
        if win:
            b = clock(win.group(2))
            a = clock(win.group(1), pm_guess=bool(b and b >= '12:00' and int(win.group(1).split(':')[0].split()[0]) < 12))
            e['start'], e['window_end'] = a, b
        elif s in SOFT:
            e['start'] = setting(SOFT[s])
        elif aft and aft.group(1) not in ('lunch', 'dinner'):
            want = aft.group(1).replace('appointment', '').strip()
            evs = [x for x in read_table('existing-events') if want[:4] in x['title'].lower()]
            hit = next((x for x in evs if x['date'] == e['date']), None)
            if not hit and not (e.get('day') or '').strip():
                # no day said: the next time that event happens, from now on
                now = today_of(ctx).strftime('%Y-%m-%d %H:%M')
                hit = min((x for x in evs if x['date'] + ' ' + x['start'] >= now), key=lambda x: x['date'] + x['start'], default=None)
                e['date'] = hit['date'] if hit else e['date']
            e['start'] = hit['end'] if hit else None
            if not hit:
                ask(ctx, 'Which event is "%s" on %s?' % (aft.group(1), e['date']))
        else:
            e['start'] = clock(s) if s else None
        how(ctx, '"%s" from %s is %s, start "%s" is %s' % (e.get('day') or 'no day said, so today', today.strftime('%a %d %b'), e['date'], s or 'not said', e.get('start') or 'not known'))
        out.append(e)
    return done(ctx, out)


# ---------- how long ----------

DUR_WORDS = {'an hour and a half': 90, 'an hour and 30 minutes': 90, 'one hour': 60, 'an hour': 60, '1 hour': 60,
             'half an hour': 30, 'two hours': 120, 'three hours': 180, 'just a quick one': 15}
HALF = {'morning': 'half_day_morning', 'afternoon': 'half_day_afternoon', 'evening': 'half_day_evening'}


def minutes_of(p):
    p = re.sub(r'^for\s+', '', (p or '').strip().lower())
    if p in DUR_WORDS:
        return DUR_WORDS[p]
    m = re.match(r'^(\d+(?:\.\d+)?)\s*(h|hr|hrs|hour|hours|m|min|mins|minute|minutes)$', p)
    if m:
        n = float(m.group(1))
        return int(n * 60) if m.group(2).startswith('h') else int(n)
    return None


def fill_length(events, ctx):
    """All day, half a day, a length, an end time, a range, quick, or the usual length from durations.csv.
    A start that contradicts the length is not guessed: the row asks."""
    known = {r['activity'].lower(): int(r['minutes']) for r in read_table('durations') if r.get('minutes', '').isdigit()}
    said = (ctx.get('input') or '').lower()
    out = []
    for e in events or []:
        e = dict(e)
        L = (e.get('length') or '').strip().lower()
        end = (e.get('end') or '').strip().lower()
        what = (e.get('title') or '').lower()
        rng = re.match(r'^(?:from\s+)?(\S+(?:\s*[ap]m)?)\s+(?:to|till|until|-)\s+(\S+(?:\s*[ap]m)?)$', L) or \
            re.match(r'^(?:from\s+)?(\S+(?:\s*[ap]m)?)\s+(?:to|till|until|-)\s+(\S+(?:\s*[ap]m)?)$', end)
        half = next((k for k in HALF if k in L and ('the ' + k in L or 'whole ' + k in L or 'keep the' in L)), None)
        if 'all day' in L or 'all day' in said:
            if e.get('start'):
                ask(ctx, '%s: all day, or from %s?' % (e['title'], e['start']))
            e.update(allDay=True, start=None, end=None)
        elif half:
            a, b = setting(HALF[half], '08:00-12:00').split('-')
            if e.get('start') and not (a <= e['start'] < b):
                ask(ctx, '%s: the %s, or from %s?' % (e['title'], half, e['start']))
            e.update(start=a, end=b)
        elif rng:
            b = clock(rng.group(2))
            a = clock(rng.group(1), pm_guess=bool(b and b >= '12:00'))
            if e.get('start') and e['start'] != a:
                ask(ctx, '%s: from %s, or from %s?' % (e['title'], e['start'], a))
            e.update(start=a, end=b)
        elif end or L.startswith('until') or L.startswith('till'):
            e['end'] = clock(re.sub(r'^(until|till)\s+', '', end or L))
        elif 'quick' in L or 'quick' in what:
            e['minutes'] = int(setting('quick_minutes', '15'))
        elif minutes_of(L):
            e['minutes'] = minutes_of(L)
        else:
            e['minutes'] = next((m for a, m in sorted(known.items(), key=lambda x: -len(x[0])) if a in what), int(setting('default_minutes', '30')))
        if not e.get('allDay') and e.get('start') and not e.get('end'):
            if e.get('window_end') and not L:
                e['end'] = e['window_end']
            else:
                e['end'] = (datetime.strptime(e['start'], '%H:%M') + timedelta(minutes=int(e.get('minutes') or 30))).strftime('%H:%M')
        if e.get('end') and e.get('start') and e['end'] <= e['start']:
            e['end'] = '%02d:%s' % (int(e['end'][:2]) + 12, e['end'][3:]) if int(e['end'][:2]) < 12 else e['end']
        e.pop('window_end', None)
        how(ctx, 'all day' if e.get('allDay') else '%s to %s, %s' % (e.get('start'), e.get('end'), ('from "%s"' % (L or end)) if (L or end) else 'not said, so %s min (durations, else the default)' % e.get('minutes')))
        out.append(e)
    return done(ctx, out)


# ---------- repeat ----------

UNTIL = {'christmas': '12-25', 'the end of the year': '12-31', 'end of the year': '12-31', 'new year': '12-31'}


def resolve_repeat(events, ctx):
    """The repeat words as an RFC 5545 RRULE."""
    out = []
    for e in events or []:
        e = dict(e)
        p = (e.get('repeat') or '').strip().lower()
        d = datetime.strptime(e['date'], '%Y-%m-%d').date()
        rule = None
        if p:
            # the 5th weekday of a month is not in every month, so it is the last one (-1)
            nth = (d.day - 1) // 7 + 1 if d.day <= 28 else -1
            named = [BYDAY[i] for i, n in enumerate(DAYS) if re.search(r'\b' + n + r's?\b', p)]
            if re.search(r'every (week|working )day|monday to friday|weekdays|every weekday', p):
                rule = 'FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR'
            elif re.search(r'every day|daily|every (morning|evening|night)', p):
                rule = 'FREQ=DAILY'
            elif re.search(r'every other|every second|fortnightly|every (2|two) weeks|biweekly', p):
                rule = 'FREQ=WEEKLY;INTERVAL=2'
            elif re.search(r'(month|monthly)', p) and (named or re.search(r'same weekday|first|second|third|fourth|last', p)):
                rule = 'FREQ=MONTHLY;BYDAY=%d%s' % (nth, BYDAY[d.weekday()])
            elif re.search(r'every month|monthly|every \d{1,2}(st|nd|rd|th)\b', p):
                rule = 'FREQ=MONTHLY;BYMONTHDAY=%d' % (d.day if d.day <= 28 else -1)
            elif re.search(r'every year|yearly|annually', p):
                rule = 'FREQ=YEARLY'
            elif re.search(r'every (week|' + '|'.join(DAYS) + r')|weekly|once a week|same time every week|for (\d+) weeks', p):
                rule = 'FREQ=WEEKLY'
                if len(named) > 1:
                    rule += ';BYDAY=' + ','.join(named)
            c = re.search(r'for (\d+|two|three|four|five|six) (weeks|times|months|days)', p)
            if c and rule and 'UNTIL' not in rule:
                n = c.group(1)
                rule += ';COUNT=' + (n if n.isdigit() else str(['two', 'three', 'four', 'five', 'six'].index(n) + 2))
            m = re.search(r'until (.+)$', p)
            if m and rule:
                u = m.group(1).strip()
                md = UNTIL.get(u)
                em = re.match(r'^(?:the )?end of ([a-z]+)$', u)
                if md:
                    ud = date(d.year, int(md[:2]), int(md[3:]))
                    ud = ud if ud >= d else date(d.year + 1, ud.month, ud.day)
                elif em and any(n.startswith(em.group(1)[:3]) for n in MONTHS):
                    mo = next(i for i, n in enumerate(MONTHS) if n.startswith(em.group(1)[:3])) + 1
                    y = d.year if mo >= d.month else d.year + 1
                    ud = date(y + (mo == 12), mo % 12 + 1, 1) - timedelta(days=1)
                else:
                    try:
                        ud = resolve_day(u, d)
                    except ValueError:
                        ud = None
                if ud:
                    rule += ';UNTIL=%s' % ud.strftime('%Y%m%d')
            if not rule:
                ask(ctx, 'How often does %s repeat?' % e['title'])
        e['rrule'] = rule
        how(ctx, ('"%s" is %s' % (p, rule or 'not readable')) if p else 'not said, so it happens once')
        out.append(e)
    return done(ctx, out)


# ---------- where, who, which calendar ----------

def resolve_place(events, ctx):
    """Her spelling of places from places.csv, online meetings as Teams or the link, a trip as from -> to."""
    places = read_table('places')
    out = []
    for e in events or []:
        e = dict(e)
        p = re.sub(r'^(at|in|on)\s+', '', (e.get('where') or '').strip(), flags=re.I).strip()
        trip = re.match(r'^from (.+?) to (.+)$', p, re.I)
        e['online'] = False
        if trip:
            p = '%s -> %s' % (trip.group(1).strip(), trip.group(2).strip())
        elif re.search(r'https?://', p):
            p = re.search(r'https?://\S+', p).group().rstrip('.,')
            e['online'] = True
        else:
            low = re.sub(r'^the\s+', '', p.lower())
            names = lambda r: [re.sub(r'^the\s+', '', n) for n in [r['place'].lower()] + split_list(r.get('also_written_as'))]
            hit = next((r for r in places if low in names(r)), None)
            if hit:
                p = hit['place']
                e['online'] = hit.get('address') == 'online'
        e['location'] = p
        how(ctx, ('"%s" is %s' % (e.get('where'), e['location'])) if e.get('where') else 'no place said')
        out.append(e)
    return done(ctx, out)


def calendar_votes(e):
    """Every calendar a part of the event points at: the title, the people, the place."""
    cals = read_table('calendars')
    people = read_table('people')
    votes = {}
    text = (e.get('title') or '').lower()
    for r in cals:
        if r.get('use') == 'yes' and any(re.search(r'\b' + re.escape(k) + r'\b', text) for k in split_list(r.get('keywords'))):
            votes.setdefault('title', set()).add(r['calendar'])
    for w in e.get('who') or []:
        lw = w.lower()
        hits = {r['calendar'] for r in people if lw in [r['person'].lower(), r['email'].lower()] + split_list(r.get('also_written_as'))}
        if len(hits) == 1:
            votes.setdefault('people', set()).update(hits)
    loc = (e.get('location') or '').lower()
    for r in cals:
        # a keyword as a whole word of the place, so mum's points at mum
        if r.get('use') == 'yes' and loc and any(re.search(r'\b' + re.escape(k) + r'\b', loc) for k in split_list(r.get('keywords'))):
            votes.setdefault('place', set()).add(r['calendar'])
    return votes


def pick_calendar(events, ctx):
    """Which calendar: the one every part agrees on. Parts that disagree, or nothing at all, ask her."""
    out = []
    for e in events or []:
        e = dict(e)
        v = calendar_votes(e)
        if not v:   # the intake can drop the people into the notes, so the people named anywhere in the request
            w = re.findall(r"[\w@.]+", (ctx.get('input') or '').lower())
            v = calendar_votes({'who': w + [' '.join(x) for x in zip(w, w[1:])]})
        allc = set().union(*v.values()) if v else set()
        first = v.get('title') or v.get('people') or v.get('place') or set()
        if len(allc) == 1:
            e['calendar'] = allc.pop()
        else:
            e['calendar'] = None
            if allc:   # parts that disagree ask with the choices, nothing at all is left to the required check
                ask(ctx, 'Which calendar is %s on? %s' % (e['title'], ' or '.join(sorted(allc))), 'calendar')
        how(ctx, (', '.join('%s points at %s' % (k, ' or '.join(sorted(x))) for k, x in v.items()) or 'nothing points at a calendar') + (', so %s' % e['calendar'] if e['calendar'] else ', so it asks'))
        out.append(e)
    return done(ctx, out)


def resolve_people(events, ctx):
    """Names and emails from people.csv. Two people with the same name: the one on the event's calendar."""
    people = read_table('people')
    out = []
    for e in events or []:
        e = dict(e)
        found = []
        for w in e.get('who') or []:
            lw = w.lower().strip()
            hits = [r for r in people if lw in [r['person'].lower(), r['email'].lower()] + split_list(r.get('also_written_as'))]
            if len(hits) > 1:
                hits = [r for r in hits if r['calendar'] == e.get('calendar')] or hits
            if len(hits) > 1:
                ask(ctx, 'Which %s?' % w)
            found.append(hits[0] if hits else {'person': w, 'email': w if '@' in w else ''})
        e['attendees'] = [{'name': r['person'], 'email': r['email']} for r in found]
        how(ctx, ', '.join('"%s" is %s' % (w, a['name']) for w, a in zip(e.get('who') or [], e['attendees'])) or 'nobody named')
        out.append(e)
    return done(ctx, out)


# ---------- notes ----------

def add_notes(events, ctx):
    """The notes as said, a "remind me" becomes a reminder instead."""
    out = []
    for e in events or []:
        e = dict(e)
        n = (e.get('notes') or '').strip()
        m = re.search(r'remind me (the day before|a day before|\d+ (?:minutes|hours) before)', n, re.I)
        if m:
            e['reminder_minutes'] = 1440 if 'day' in m.group(1).lower() else minutes_of(m.group(1).lower().replace(' before', ''))
            n = (n[:m.start()] + n[m.end():]).strip(' .')
        e['desc'] = n
        how(ctx, ('a reminder %s min before, ' % e['reminder_minutes'] if e.get('reminder_minutes') else '') + ('kept as you said it' if e.get('desc') else 'nothing to note'))
        out.append(e)
    return done(ctx, out)


# ---------- clashes ----------

ANYWAY = r"(book|put) (it )?in anyway|book (it )?anyway|even if (it|something) (clashes|overlaps)|double[ -]?book|ignore (the )?clash"


def day_events(d, ctx):
    """What is already in the calendars on that day: existing-events.csv in the test, Google Calendar events_on in real use."""
    if ctx.get('test_today'):
        return [dict(x, allDay=False) for x in read_table('existing-events') if x['date'] == d]
    import connections
    try:
        evs = connections.call('google-calendar', 'events_on', {'date': d}) or []
    except Exception:
        return []
    return [{'title': x.get('title', ''), 'start': (x.get('start') or '')[11:16], 'end': (x.get('end') or '')[11:16],
             'allDay': x.get('allDay'), 'calendar': x.get('cal', '')} for x in evs]


def check_clash(events, ctx):
    """Is anything in the way? An overlap with an event already there becomes a question, unless the request says book anyway.
    All-day events and the test's own TEST events never clash."""
    anyway = re.search(ANYWAY, (ctx.get('input') or '').lower())
    out = []
    for e in events or []:
        e = dict(e, clash='')
        if e.get('allDay') or not (e.get('date') and e.get('start') and e.get('end')):
            how(ctx, 'all day, nothing can clash' if e.get('allDay') else 'no time yet, nothing to check')
            out.append(e)
            continue
        hits = [x for x in day_events(e['date'], ctx) if not x.get('allDay') and x.get('start') and x.get('end')
                and not x['title'].upper().startswith('TEST') and x['start'] < e['end'] and e['start'] < x['end']]
        if hits:
            e['clash'] = '; '.join('%s %s-%s' % (x['title'], x['start'], x['end']) for x in hits)
            if anyway:
                how(ctx, 'overlaps %s, booked anyway as you said' % e['clash'])
            else:
                ask(ctx, '%s overlaps %s, book anyway?' % (e.get('title') or e.get('what'), e['clash']), 'clash')
                how(ctx, 'overlaps ' + e['clash'])
        else:
            how(ctx, 'nothing in the way on %s' % e['date'])
        out.append(e)
    return done(ctx, out)


# ---------- the answer, and putting it in ----------

CONFIRM = r"check with me|confirm (it |with me )?first|ask me (first|before)|let me (check|confirm)|before you (put|book|add)|run it by me"


def finish(events, ctx):
    """The final event rows. Any question a check raised on the way is kept in asks for the Ask step.
    With no questions and no confirm wanted (settings.csv confirm, or "check with me first"), the rows go straight in."""
    keep = ['title', 'date', 'start', 'end', 'allDay', 'location', 'online', 'attendees', 'calendar', 'rrule', 'desc', 'reminder_minutes']
    # the notes come from their own model step, one per event in the same order
    notes = ctx.get('notes') if isinstance(ctx.get('notes'), list) else []
    events = [dict(e, **{k: v for k, v in (notes[i] if i < len(notes) and isinstance(notes[i], dict) else {}).items() if k in ('desc', 'reminder_minutes') and v})
              for i, e in enumerate(events or [])]
    rows = [{k: e.get(k) for k in keep} for e in events]
    confirm = setting('confirm', 'false').lower() == 'true' or re.search(CONFIRM, (ctx.get('input') or '').lower())
    if confirm and rows and not ctx.get('asks'):
        for r in rows:
            ask(ctx, 'Put in %s on %s %s in %s?' % (r['title'], r['date'], 'all day' if r.get('allDay') else '%s-%s' % (r.get('start'), r.get('end')), r.get('calendar')), 'confirm')
    ctx['asks'] = ctx.get('asks') or None
    ctx['to_create'] = None if ctx['asks'] else rows or None
    ctx['_note'] = '%d question%s for you' % (len(ctx['asks']), '' if len(ctx['asks']) == 1 else 's') if ctx['asks'] else 'Nothing to ask, straight in'
    return {'events': rows, 'asks': ctx['asks'] or []}


def put_in(rows, ctx):
    """Readies the rows for Put it in: finds the calendar to write into (in the test always settings.csv test_calendar,
    each event titled TEST and the calendar it was meant for). No separate calendars step."""
    test = bool(ctx.get('test_today'))
    if test:
        rows = [dict(e, title='TEST %s - %s [%s]' % (ctx.get('test_id', ''), e['title'], e.get('calendar') or '?'),
                     desc=chr(10).join(x for x in [e.get('desc') or '', 'With: ' + ', '.join(a['name'] for a in e.get('attendees') or []) if e.get('attendees') else ''] if x))
                for e in rows or []]
    cals = None
    if not ctx.get('dry'):
        import connections
        cals = connections.call('google-calendar', 'list_calendars', {}) or []
        cals = cals.get('calendars', cals) if isinstance(cals, dict) else cals
    out = []
    for e in rows or []:
        name = setting('test_calendar', 'IU') if test else e.get('calendar')
        if cals is None:
            cid = 'calendar called %s' % name   # dry run: not looked up
        else:
            cal = next((c for c in cals if c['name'].strip() == name), None)
            if not cal:
                raise ValueError('no calendar called %s' % name)
            cid = cal['id']
        out.append(dict(e, calId=cid))
    ctx['_note'] = 'Into %s' % ', '.join(sorted({setting('test_calendar', 'IU') if test else (e.get('calendar') or '?') for e in out}))
    return out


STEPS = {'thesis_title': make_title, 'thesis_when': resolve_when, 'thesis_length': fill_length, 'thesis_repeat': resolve_repeat,
         'thesis_place': resolve_place, 'thesis_calendar': pick_calendar, 'thesis_people': resolve_people,
         'thesis_notes': add_notes, 'thesis_clash': check_clash, 'thesis_finish': finish, 'thesis_put_in': put_in}
