"""Code steps for the calendar flows. No model anywhere in here: dates, calendars and travel buffers
are worked out the same way every time, from the calendar area's tables in My Data/knowledge/calendar:

  calendars.csv     calendar, keywords, use (yes / no / travel)
  places.csv        place, also_written_as, address
  travel-times.csv  from, to, minutes, seen, source
  settings.csv      setting, value

Each step is fn(value, ctx): value is the step's input, ctx everything the flow has so far.
"""
import csv
import os
import re
from datetime import date, datetime, timedelta

KNOW = None   # set by papertrail: My Data/knowledge
AREA = 'calendar'
DAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
MONTHS = ['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september',
          'october', 'november', 'december']


def now():
    return datetime.now()


# ---------- the calendar area's tables ----------

def table_path(name):
    return os.path.join(KNOW, AREA, name + '.csv')


def read_table(name):
    try:
        with open(table_path(name), encoding='utf-8-sig', newline='') as f:
            return [{k: (v or '').strip() for k, v in r.items()} for r in csv.DictReader(f)]
    except FileNotFoundError:
        return []


def write_table(name, header, rows):
    """Saved with a BOM so Excel opens the accents (Komenského) correctly."""
    with open(table_path(name), 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        w.writerows(rows)


def split_list(v):
    return [x.strip().lower() for x in (v or '').split(';') if x.strip()]


def cfg():
    """All four tables in the shape the steps use."""
    st = {r['setting']: r['value'] for r in read_table('settings')}
    cals = read_table('calendars')
    places = read_table('places')
    spelling, address = {}, {}
    for r in places:
        for w in [r['place']] + split_list(r.get('also_written_as')):
            spelling[w.lower()] = r['place']
        if r.get('address'):
            address[r['place'].lower()] = r['address']
    return {'home': st.get('home', 'Home'), 'timezone': st.get('timezone', 'Europe/Bratislava'),
            'default_minutes': int(st.get('default_minutes') or 30),
            'maps_padding_minutes': int(st.get('maps_padding_minutes') or 15),
            'tomorrow_means_today_before': st.get('tomorrow_means_today_before') or '05:00',
            'calendars': {r['calendar']: {'keywords': split_list(r.get('keywords'))} for r in cals if r.get('use') != 'no'},
            'never_use': [r['calendar'] for r in cals if r.get('use') == 'no'],
            'travel_calendar': next((r['calendar'] for r in cals if r.get('use') == 'travel'), 'Travelling Buffer'),
            'place_spelling': spelling, 'address': address}


def load_rules(_, ctx):
    """First step: the tables, and the calendar names the model may pick from."""
    c = cfg()
    ctx['calendar_names'] = ', '.join(n for n in c['calendars'] if n != c['travel_calendar'])
    return {'calendars': len(c['calendars']), 'places': len(set(c['place_spelling'].values())), 'home': c['home']}


def spell(text):
    """Her spelling of places, e.g. park is always Aupark. One pass, longest first, so
    "la putika" never turns into "La La Putika"."""
    if not text:
        return text
    sp = cfg()['place_spelling']
    keys = sorted(sp, key=len, reverse=True)
    pat = re.compile(r'(?<![\w])(' + '|'.join(re.escape(k) for k in keys) + r')(?![\w])', re.I)
    return pat.sub(lambda m: sp[m.group(1).lower()], text)


def make_title(events, ctx):
    """The title, built from the pieces the model found and the patterns in naming.csv."""
    pats = {r['rule']: r['pattern'] for r in read_table('naming')}
    said = (ctx.get('input') or '').lower()
    out = []
    for e in events or []:
        e = dict(e)
        # the model's flags only count when the request really says so, checked here in code
        if not re.search(r'\b(every|weekly|daily|each)\b', said):
            e['often'] = ''
        if not re.search(r'\b(maybe|not sure|might|tentative)\b', said):
            e['maybe'] = False
        if not re.search(r'not agreed|not confirmed|booked|no invit', said):
            e['status'] = ''
        if not re.search(r'priorit|urgent|important|asap', said):
            e['priority'] = ""
        # a calendar name is not a place: "in Me Time" says which calendar, not where to travel
        if norm_place(e.get('location')) in {norm_place(n) for n in cfg()['calendars']}:
            e['location'] = ''
        what = spell((e.get('what') or e.get('title') or '').strip())
        what = what[:1].upper() + what[1:]
        who = e.get('who') or ''
        who = ' and '.join(w.strip() for w in who if w.strip()) if isinstance(who, list) else who.strip()
        if who:   # the names go after "with" once, not inside what as well
            what = re.sub(r'\s+with\s+.*$', '', what, flags=re.I).strip() or what
        t = pats.get('with_who', '{What} with {who}') if who else pats.get('plain', '{What}')
        title = t.replace('{What}', what).replace('{who}', who)
        if e.get('source'):
            title = pats.get('source', '{title} ({source})').replace('{title}', title).replace('{source}', e['source'])
        if e.get('often'):
            title = pats.get('recurring', '{title} - {often}').replace('{title}', title).replace('{often}', e['often'].capitalize())
        status = (e.get('status') or '').lower()
        client = (e.get('client') or '').upper()
        if status == 'not agreed' and client:
            title = pats['not_agreed'].replace('{CLIENT}', client).replace('{title}', title)
        elif status == 'booked, no invite' and client:
            title = pats['booked_no_invite'].replace('{CLIENT}', client).replace('{title}', title)
        elif str(e.get('maybe')).lower() == 'true':
            title = pats.get('maybe', 'MAYBE {title}').replace('{title}', title)
        out.append(dict(e, title=title))
    return out


# ---------- travel table: learnt from the calendar, and from your answers ----------

TRAVEL_HEADER = ['from', 'to', 'minutes', 'seen', 'source']


def load_travel():
    return {r['from'] + ' -> ' + r['to']: {'minutes': int(r['minutes']), 'seen': r.get('seen', ''), 'source': r.get('source', '')}
            for r in read_table('travel-times') if r.get('minutes', '').isdigit()}


def save_travel(table):
    rows = [{'from': k.split(' -> ')[0], 'to': k.split(' -> ')[1], 'minutes': v['minutes'], 'seen': v.get('seen', ''),
             'source': v.get('source', '')} for k, v in sorted(table.items())]
    write_table('travel-times', TRAVEL_HEADER, rows)


def norm_place(p):
    return re.sub(r'\s+', ' ', (p or '').strip()).lower()


def learn_travel(existing, ctx):
    """Every 'A -> B' event already in the calendar teaches how long A to B takes. Newest wins,
    except a time you gave yourself, which the calendar never overwrites."""
    table = load_travel()
    learnt = 0
    for e in sorted((existing or {}).get('events', []), key=lambda e: e.get('start', '')):
        t = e.get('title') or ''
        m = re.match(r'^\s*(.+?)\s*(?:->|→)\s*(.+?)\s*$', t)
        if not m or e.get('allDay'):
            continue
        try:
            mins = int((datetime.fromisoformat(e['end']) - datetime.fromisoformat(e['start'])).total_seconds() // 60)
        except Exception:
            continue
        key = m.group(1).strip() + ' -> ' + m.group(2).strip()
        if 0 < mins <= 240 and table.get(key, {}).get('source') != 'you':
            table[key] = {'minutes': mins, 'seen': e['start'][:10], 'source': 'calendar'}
            learnt += 1
    save_travel(table)
    return {'routes': len(table), 'read_from_calendar': learnt}


def save_route(route, minutes):
    """An answer you gave once, kept for good."""
    table = load_travel()
    table[route] = {'minutes': int(minutes), 'seen': date.today().isoformat(), 'source': 'you'}
    save_travel(table)


class NeedAnswer(Exception):
    """The flow stops and asks you, the answers are saved and the request runs again."""
    def __init__(self, questions):
        super().__init__('waiting for you')
        self.questions = questions


ADDRESS = re.compile(r'\d|,')


def is_address(p):
    """A real address (a number or a comma in it) goes to Maps, a short name like Aupark is looked up or asked."""
    return bool(p and ADDRESS.search(p))


def maps_minutes(a, b, leave):
    """Travel time from Google Maps with traffic at the time of leaving, plus Leo's 15 minutes. None if Maps is not set up."""
    try:
        import connections
        adr = cfg()['address']
        r = connections.call('google-maps', 'travel_time', {'origin': adr.get(a.lower(), a), 'destination': adr.get(b.lower(), b), 'depart_at': leave})
        pad = cfg().get('maps_padding_minutes', 15)
        return int(-(-(r['minutes'] + pad) // 5) * 5)   # rounded up to 5 minutes
    except Exception:
        return None


def travel_minutes(a, b):
    for k, v in load_travel().items():
        x, _, y = k.partition(' -> ')
        if norm_place(x) == norm_place(a) and norm_place(y) == norm_place(b):
            return v['minutes']
    return None


# ---------- dates: "next friday", "in 10 days", "24.10.", "tomorrow" ----------

def resolve_day(phrase, today=None):
    p = (phrase or '').strip().lower()
    today = today or now().date()
    if now().strftime('%H:%M') < cfg().get('tomorrow_means_today_before', '05:00') and p == 'tomorrow':
        return today   # her midnight rule: still the night before
    if not p or p == 'today':
        return today
    if p == 'tomorrow':
        return today + timedelta(days=1)
    if p in ('day after tomorrow', 'the day after tomorrow'):
        return today + timedelta(days=2)
    m = re.match(r'^in (\d+) (day|days|week|weeks)$', p)
    if m:
        n = int(m.group(1))
        return today + timedelta(days=n * (7 if m.group(2).startswith('week') else 1))
    m = re.match(r'^(\d{4})-(\d{1,2})-(\d{1,2})$', p)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.match(r'^(\d{1,2})\.\s*(\d{1,2})\.?\s*(\d{2,4})?$', p)
    if m:
        y = int(m.group(3)) if m.group(3) else today.year
        y = y + 2000 if y < 100 else y
        d = date(y, int(m.group(2)), int(m.group(1)))
        return d if m.group(3) or d >= today else date(y + 1, d.month, d.day)
    m = re.match(r'^(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?([a-z]+)$', p) or re.match(r'^([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?$', p)
    if m:
        a, b = m.groups()
        day, mon = (a, b) if a.isdigit() else (b, a)
        mi = next((i for i, n in enumerate(MONTHS) if n.startswith(mon[:3])), None)
        if mi is not None:
            d = date(today.year, mi + 1, int(day))
            return d if d >= today else date(today.year + 1, mi + 1, int(day))
    m = re.match(r'^(this |next |on )?([a-z]+)$', p)
    if m:
        wd = next((i for i, n in enumerate(DAYS) if n.startswith(m.group(2)[:3])), None)
        if wd is not None:
            # "friday" and "this friday" can be today, "next friday" never is
            ahead = (wd - today.weekday()) % 7
            if m.group(1) == 'next ' and ahead == 0:
                ahead = 7
            return today + timedelta(days=ahead)
    raise ValueError('cannot read the day "%s"' % phrase)


def hhmm(t):
    t = (t or '').strip().lower().replace('.', ':')
    m = re.match(r'^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$', t)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    if m.group(3) == 'pm' and h < 12:
        h += 12
    if m.group(3) == 'am' and h == 12:
        h = 0
    return '%02d:%02d' % (h, mi)


def resolve_dates(events, ctx):
    """The model only says which day in words, code turns it into a date and fills a missing end."""
    out, asks = [], []
    for e in events or []:
        e = dict(e)
        e['date'] = resolve_day(e.get('day')).isoformat()
        start = hhmm(e.get('start'))
        said = (ctx.get('input') or '').lower()
        if not start and not any(w in said for w in ('all day', 'all-day', 'whole day')):
            # no time said and not all day: ask rather than guess
            asks.append({'type': 'add', 'text': 'What time is %s on %s?' % (e.get('title') or e.get('what'), e['date']),
                         'placeholder': 'e.g. 10:00 till 11:00'})
            continue
        if not start:
            e['allDay'] = True
            e.pop('start', None)
            e.pop('end', None)
        else:
            e['start'] = start
            e['end'] = hhmm(e.get('end')) or ''
        out.append(e)
    if asks:
        raise NeedAnswer(asks)
    return out


def pick_calendar(events, ctx):
    """Which calendar, decided by code, never by the model: a calendar named in the request
    ("on Friends"), else a keyword from calendars.csv in what, who or where, else ask once and
    save the keyword. Never a calendar marked no. Places get her spelling here too."""
    c = cfg()
    never = set(c.get('never_use', []))
    cals = [x for x in (ctx.get('calendars') or []) if x['id'] not in never and not x.get('ro')
            and norm_place(x['name']) != norm_place(c['travel_calendar'])]
    said = ' ' + (ctx.get('input') or '').lower() + ' '
    out, asks = [], []
    for e in events or []:
        e = dict(e, title=spell(e.get('title')), location=spell(e.get('location')))
        who = e.get('who') or ''
        who = ' '.join(who) if isinstance(who, list) else who
        text = ' '.join([e.get('title') or '', e.get('what') or '', who, e.get('location') or '']).lower()
        named = next((x['name'].strip() for x in cals if re.search(r'\b(on|in|into|to)\s+(my\s+)?' + re.escape(norm_place(x['name'])) + r'\b', said)), None)
        rule = named or next((name for name, v in c['calendars'].items() if any(k in text for k in v.get('keywords', []))), None)
        hit = rule and next((x for x in cals if norm_place(x['name']) == norm_place(rule)), None)
        if not hit:
            what = (e.get('what') or e.get('title') or '').strip().lower()
            asks.append({'type': 'calendar', 'match': what, 'text': 'Which calendar is %s on?' % e['title'],
                         'options': [{'label': x['name'].strip(), 'add': 'on ' + x['name'].strip(), 'save': x['name'].strip()} for x in cals]})
            continue
        out.append(dict(e, calId=hit['id'], calendar=hit['name'].strip(), chosen_by='named' if named else 'keyword'))
    if asks:
        raise NeedAnswer(asks)
    return out


def save_calendar_keyword(keyword, calendar):
    """Your answer to "which calendar", kept as a keyword so it is never asked again."""
    rows = read_table('calendars')
    for r in rows:
        if r['calendar'].strip().lower() == calendar.strip().lower():
            kws = split_list(r.get('keywords'))
            if keyword and keyword.lower() not in kws:
                kws.append(keyword.lower())
            r['keywords'] = '; '.join(kws)
    write_table('calendars', ['calendar', 'keywords', 'use'], rows)


ONLINE = re.compile(r'online|teams|zoom|google meet|meet\.google|webex|https?://', re.I)


def is_place(loc):
    """A real place to travel to, not an online meeting link."""
    return bool(loc and loc.strip()) and not ONLINE.search(loc)


def add_travel(events, ctx):
    """A buffer there and back for every event with a place, from the travel table.
    Where she comes from is the place of the event before it that day, else Home."""
    existing = (ctx.get('existing') or {}).get('events', [])
    conf = cfg()
    BUFFER_CALENDAR, HOME = conf['travel_calendar'], conf['home']
    buf = next((c for c in ctx.get('calendars') or [] if norm_place(c['name']) == norm_place(BUFFER_CALENDAR)), None)
    out, notes, asks = [], [], []
    for e in events or []:
        out.append(e)
        dest = (e.get('location') or '').strip()
        if not is_place(dest) or e.get('allDay') or e.get('online'):
            continue
        # a buffer's location is where she ends up, so buffers count as places too
        day = [x for x in existing if (x.get('start') or '')[:10] == e['date'] and is_place(x.get('location'))]
        before = [x for x in day if x['end'][11:16] <= e['start']]
        after = [x for x in day if x['start'][11:16] >= e['end']]
        frm = before[-1]['location'] if before else HOME
        back = after[0]['location'] if after else HOME
        for a, b, when in ((frm, dest, 'there'), (dest, back, 'back')):
            if norm_place(a) == norm_place(b):
                continue
            mins = travel_minutes(a, b)
            adr = conf['address']
            if mins is None and (is_address(a) or is_address(b) or a.lower() in adr or b.lower() in adr):
                leave = e['date'] + 'T' + (e['start'] if when == 'there' else e['end'])
                mins = maps_minutes(a, b, leave)
                if mins is not None:
                    notes.append('%s -> %s from Google Maps with traffic, %d min' % (a, b, mins))
            if mins is None:
                q = '%s -> %s' % (a, b)
                if q not in asks:
                    asks.append(q)
                continue
            if when == 'there':
                s = (datetime.strptime(e['start'], '%H:%M') - timedelta(minutes=mins)).strftime('%H:%M')
                out.insert(len(out) - 1, {'title': '%s -> %s' % (a, b), 'date': e['date'], 'start': s, 'end': e['start'],
                                          'location': b, 'calId': buf and buf['id'], 'calendar': BUFFER_CALENDAR})
            else:
                en = (datetime.strptime(e['end'], '%H:%M') + timedelta(minutes=mins)).strftime('%H:%M')
                out.append({'title': '%s -> %s' % (a, b), 'date': e['date'], 'start': e['end'], 'end': en,
                            'location': b, 'calId': buf and buf['id'], 'calendar': BUFFER_CALENDAR})
    if asks:
        raise NeedAnswer([{'type': 'travel', 'route': q, 'text': 'How many minutes from %s?' % q.replace(' -> ', ' to ')} for q in asks])
    ctx['travel_notes'] = notes
    return out


# ---------- how long: said, known from durations.csv, or asked once ----------

def fill_length(events, ctx):
    """End time said? Done. A length said? Use it. Otherwise the usual length of this activity from
    durations.csv, and if it is not there yet, ask once and remember."""
    known = {r['activity'].lower(): int(r['minutes']) for r in read_table('durations') if r.get('minutes', '').isdigit()}
    out, asks = [], []
    for e in events or []:
        e = dict(e)
        if e.get('allDay') or e.get('end'):
            out.append(e)
            continue
        what = (e.get('what') or e.get('title') or '').strip().lower()
        mins = int(e['minutes']) if str(e.get('minutes') or '').isdigit() else None
        if mins is None:
            mins = known.get(what) or next((m for a, m in known.items() if a and a in what), None)
        if mins is None:
            asks.append({'type': 'duration', 'activity': what, 'text': 'How long does %s usually take?' % (e.get('title') or what),
                         'unit': 'min'})
            continue
        e['minutes'] = mins
        e['end'] = (datetime.strptime(e['start'], '%H:%M') + timedelta(minutes=mins)).strftime('%H:%M')
        out.append(e)
    if asks:
        raise NeedAnswer(asks)
    return out


def save_duration(activity, minutes):
    rows = [r for r in read_table('durations') if r['activity'].lower() != activity.lower()]
    rows.append({'activity': activity.lower(), 'minutes': int(minutes), 'source': 'you'})
    write_table('durations', ['activity', 'minutes', 'source'], sorted(rows, key=lambda r: r['activity']))


# ---------- already booked: free, inside something that allows it, or ask ----------

def booking_type(ev):
    """fixed, flexible or container, from booking-types.csv. Unknown means fixed until you say otherwise."""
    title, cal = (ev.get('title') or '').lower(), (ev.get('cal') or '').strip().lower()
    for r in read_table('booking-types'):
        m = r['match'].strip().lower()
        if (r['by'] == 'calendar' and m == cal) or (r['by'] == 'title' and m and m in title):
            return r['type'], r
    return 'fixed', None


def check_clash(events, ctx):
    said = (ctx.get('input') or '').lower()
    existing = [x for x in (ctx.get('existing') or {}).get('events', []) if not x.get('allDay')]
    out, asks, notes = [], [], ctx.setdefault('clash_notes', [])
    for e in events or []:
        if e.get('allDay'):
            out.append(e)
            continue
        s, en = e['date'] + 'T' + e['start'], e['date'] + 'T' + e['end']
        hits = [x for x in existing if x['start'][:16] < en and x['end'][:16] > s]
        for x in hits:
            kind, _ = booking_type(x)
            span = '%s %s-%s' % (x['title'], x['start'][11:16], x['end'][11:16])
            if kind == 'container':
                continue   # fine by definition, not worth a mention
            elif kind == 'flexible':
                n = '%s overlaps %s, which can move' % (e['title'], span)
                if n not in notes:
                    notes.append(n)
            elif 'book anyway' not in said:
                asks.append({'type': 'clash', 'text': '%s overlaps %s.' % (e['title'], span), 'match': x['title'],
                             'options': [{'label': 'Book anyway', 'add': 'book anyway'},
                                         {'label': 'Things can always go inside "%s"' % x['title'], 'add': 'book anyway', 'save': 'container'},
                                         {'label': 'Do not book', 'stop': True}]})
        out.append(e)
    if asks:
        raise NeedAnswer(asks)
    return out


def save_booking_type(match, kind):
    rows = [r for r in read_table('booking-types') if not (r['by'] == 'title' and r['match'].lower() == match.lower())]
    rows.append({'match': match, 'by': 'title', 'type': kind, 'note': 'from you'})
    write_table('booking-types', ['match', 'by', 'type', 'note'], rows)


# ---------- why and priority go into the notes ----------

def add_notes(events, ctx):
    out = []
    for e in events or []:
        lines = []
        if e.get('why'):
            lines.append('Why: ' + str(e['why']))
        if e.get('priority'):
            lines.append('Priority: ' + str(e['priority']))
        out.append(dict(e, desc='\n'.join(lines)) if lines else e)
    return out



def summarise(created, ctx):
    items = ctx.get('to_create') or []
    lines = ['%s %s-%s %s (%s)' % (e['date'], e.get('start', ''), e.get('end', ''), e['title'], e.get('calendar', ''))
             for e in items]
    say = ('Created %d: ' % len(items) if not ctx.get('dry') else 'Would create %d: ' % len(items)) + '; '.join(lines)
    links = [c.get('htmlLink') for c in (created if isinstance(created, list) else []) if isinstance(c, dict) and c.get('htmlLink')]
    extra = (ctx.get('clash_notes') or []) + (ctx.get('travel_notes') or [])
    if extra:
        say += '. ' + '; '.join(extra)
    return {'say': say, 'links': links}


STEPS = {'load_rules': load_rules, 'make_title': make_title, 'learn_travel': learn_travel, 'resolve_dates': resolve_dates, 'pick_calendar': pick_calendar,
         'add_travel': add_travel, 'summarise': summarise, 'fill_length': fill_length,
         'check_clash': check_clash, 'add_notes': add_notes}
