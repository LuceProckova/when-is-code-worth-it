"""one rulebook for every version: all-model flow and one-prompt baseline from the same rule texts

  python make_prompts.py            writes both, prints what changed
  python make_prompts.py --check    exits 1 if the files on disk differ from what this script would write

each rule block mirrors the code function named above it, change both and rerun
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DATA = os.path.join(os.path.dirname(HERE), 'data')
CODE_FLOW = os.path.join(APP_DATA, 'workflows', 'thesis-create-events.json')
ALL_FLOW = os.path.join(APP_DATA, 'workflows', 'thesis-create-events-allmodel.json')
BASELINE = os.path.join(HERE, 'baseline_prompt.txt')

ROWS = ('For each row give back only its row number and the fields this step adds, nothing else: the other fields are '
        'kept for you. The same rows, the same row numbers. A repeating event stays ONE row with an rrule, never one row per date.')

# resolve_day + words_day
DATE_RULES = '''Working out the date (YYYY-MM-DD) from the day words and today:
- No day said: today. Never change the day words: "next Wednesday" stays Wednesday. Read the day from the whole request ("next Thursday" anywhere in it), never from words after "every" or "until"; time words after the day ("Monday early") are not part of the day.
- A weekday on its own or "this <weekday>": the first such day from today, today itself counts.
- "next <weekday>" or "coming <weekday>": the first such day after today, never today itself. From Saturday 2026-11-07, "next Tuesday" is 2026-11-10.
- "in N days" is today + N, "in N weeks" or "in a week" is today + 7 x N.
- A day and month without a year ("25 November"): this year, or next year if that date has already passed. A full date with a year that has passed counts as said without a year.
- "first/second/third/fourth/last <weekday> of <month>": that weekday in that month; "this month", "next month" or a month name. If it has passed, the next month (for "this month") or next year (for a named month).
- "today", "tonight", "this morning/afternoon/evening": today. "tomorrow": today + 1, but before 05:00 it still means today. "day after tomorrow": today + 2.
- "end of the week": the coming Friday (on Saturday or Sunday: the Sunday). "end of the month": its last day. "next week": the Monday of next week. "this weekend": the coming Saturday (on Sunday: today).
- Day words you cannot turn into one date: leave date empty and ask "Which day is <event>?".'''

# said_clock
TIME_RULES = '''Reading times (HH:MM, 24 hours):
- "3 pm" 15:00, "15:00" 15:00, "noon" or "midday" 12:00. A bare hour 1 to 7 without am/pm is pm ("at 4" is 16:00); 8 to 12 stays as said.
- "half 3" or "half past 3" is 15:30, "quarter to 1" is 12:45, "quarter past 9" is 09:15 (bare hours 1 to 7 are pm here too).
- A range or window starts at its first time with the range's am/pm: "between 6 and 9 pm" 18:00, "from 1 to 4 pm" 13:00, "from 9 to 12" 09:00.
- A time after "until" or "till" is the end, never the start. A house number ("at 27 Church Road") or a date ("2026-11-16", "until 19 December") is not a time.'''

# resolve_when
WHEN_RULES = '''Start, end and all day:
- A reminder, door code, note, agenda or bring list is never an event of its own: drop such a row and put its words into the first row's notes.
- After another event ("after my physio", "after the client call", anywhere in the request, not a meal): start at the end of that event in the existing events table on the event's date. With no day said, take the next such event from today and its date. If a different start time is also said, ask "<event>: after <X> (<its end>), or at <start>?". If no such event is found and no start is said, ask "Which event is "<X>" on <date>?".
- "all day" or "the whole day": allDay true, start and end empty. If a time or a part of the day is also said, ask "<event>: all day, or from <start>?". All day plus "after my X": ask "<event>: after X, or all day?".
- Two different times in one request (two ranges, a single time plus a range like "at noon, from 2 to 5", or a soft time like "after lunch" plus a clock range): ask which one. A time inside a range ("between 4 and 6 pm") is not a second time.
- A part of the day said as a length with no clock time starts at the start of that half day ("keep the whole morning" starts 08:00).
- A part of the day said as a length ("for the morning", "the whole afternoon", "all evening", "the rest of the afternoon", "keep the morning") is the half day from the settings table (morning 08:00 to 12:00, afternoon 12:00 to 18:00, evening 18:00 to 22:00). "The rest of the <part>" starts now. A start or end said outside it: ask "<event>: the <part> (<from> to <to>), or from <start>?".
- A soft time with no clock time only sets the start, from the settings table: "in the morning" morning_start (09:00), "in the afternoon" afternoon_start (14:00), "in the evening" evening_start (19:00), "after lunch" after_lunch_start (13:30), "after dinner" after_dinner_start (20:00), "late morning" 11:00, "lunchtime" 12:00, "early" early_start (08:00) but for a lunch the lunch window start (11:30).
- "at <time>" said in the request but the start is different: ask "<event>: at <said>, or at <start>?". The same for "from X to Y": the start must be X, else ask "<event>: from <X>, or at <start>?".
- End: only an end said in the request counts ("until 6 pm", "till 11", "from 2 to 4 pm"), and only a length said in the request counts ("for 2 hours", "an hour and a half"); "quick" is quick_minutes (15). End and length both said and not agreeing: ask "<event>: until <end>, or for <N> minutes?". End said: that end. Length said: start + length. A window ("between 9 and 11") with no length: its end. Else start + the usual length from the durations table (the longest activity word found in what), else 30 minutes. Never ask about a length nobody said.
- An end earlier than the start that fits as pm is pm: "after dinner until 11" ends 23:00.
- end_date is the date, or the next day when the end is after midnight.
- No start at all: start and end stay empty (the Ask step will ask).'''

# said_repeat + resolve_repeat + until_date
REPEAT_RULES = '''How often (rrule, RFC 5545, no "RRULE:" prefix, two-letter days MO TU WE TH FR SA SU):
- Read the request itself, first matching phrase wins; a quoted message never counts:
  "every day", "daily", "every morning/evening": FREQ=DAILY
  "every weekday", "every working day", "Monday to Friday", "on weekdays": FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR
  "every other week", "every second <weekday>", "fortnightly", "every 2 weeks": FREQ=WEEKLY;INTERVAL=2
  "same weekday every month": FREQ=MONTHLY;BYDAY=<n><DAY> from the date (2nd Tuesday is 2TU, a 5th one is -1)
  "every month", "monthly": FREQ=MONTHLY;BYMONTHDAY=<day of the date>
  "every year", "yearly", "annually": FREQ=YEARLY
  "for N weeks": FREQ=WEEKLY;COUNT=N (a count, never an UNTIL)
  "every <weekday>", "weekly", "once a week": FREQ=WEEKLY (the weekday comes from the date)
- None of these but the intake gave a repeat: use it, with two-letter days.
- Read the request itself for repeat words (every, each, daily, weekly, monthly, yearly, fortnightly, for N weeks, Monday to Friday). No repeat words: rrule is null, most events happen once. Until: only from the words after "until" or "till" in the request, as ;UNTIL=YYYYMMDD at the end. "Christmas" is 25 December, "end of the year" or "New Year" is 31 December, "end of <month>" its last day, "14 March" that day. It must fall after the event's date, else the next year.
- Ask "How often does <event> repeat?" only when the request says "until <date>" or "till <date>" and has no repeat word at all; rrule empty. A request with a repeat word never gets this question: write its rrule. Ask "How often does <event> repeat, and until when?" only when the repeat words fit none of the phrases above.
- Happens once: rrule empty.'''

# make_title
TITLE_RULES = '''The title, by the naming table:
- What: the event words exactly as the request says them (from after the request word up to "with", "at", "on", the day or the time), never other words: "go through" stays "go through", "physio check" stays "physio check"; typos close to a known word fixed ("gmy" is gym), abbreviations written in full ("re" is about, "mtg" is meeting, "w/" is with), with request words and fillers dropped at the start (please, can you, schedule, set up, add, put, create, book, make, block, uh, um, the, a) and filler words anywhere ("the thing", "or whatever", "finally", "quick"), slips and abbreviations fixed, every other word kept, capital first letter. No what: ask "What is the event?". Request words left inside it (schedule, add, invite, remind me...): ask "What should "<what>" be called?".
- Who: only people the request names, each once, as the people table writes them without brackets ("Name (friend)" is Name, "my boss" is Martin, natalie@example.com is Natalie, "the team" and "the client" stay as they are), leaving out a name already in what and places. Never add a person nobody said.
- With people: "<What> with <names joined by " and ">", e.g. "Catch up with Martin and Natalie". Without: "<What>".
- Company: if a keyword from the companies table is in what, who or people, or a named person has a company in the people table (Jordan (work), Martin and the team: NW; the client: FAB), the company applies: "(<SHORT>) <title>", e.g. "(NW) Standup". The client first.
- maybe true: "MAYBE " in front, e.g. "MAYBE (NW) Drinks with Martin".'''

# check_event + place_of + said_reminder
CHECK_RULES = '''Calendar, people, place, clashes, reminder:
- calendar: one exact name from the calendars table. "A or B", or a name not in the table: ask "Which calendar is <event> on? A or B" and leave it empty unless only one fits.
- People: "with client", "w/ client", "w the team", "the client is joining" name the client or the team; "the client workshop" or "after the client call" do not. "the Fabrikam people" is the client; a misheard name counts only if the misheard-words table has it. "Mum's" is a place or a possessive, not Mum herself.
- People: only people named in the request (by name, by a spelling in also_written_as, "send X an invite") and emails written in the request; never the event itself, a place or a thing.
- Invitations: only people with invite = yes in the people table (work people) get an invitation, or anyone the request explicitly invites ("invite Sam", "send Sam an invite") or whose email it writes. Everyone else is named in the title but not invited.
- attendees: for each invited person, the row in the people table by its exact person text, else by the name without brackets, else by also_written_as; two rows fit (two people with the same first name): the one whose calendar is the event's calendar, else the one who is not on Work. Write {"name": person without brackets, "email": its email}. Never invent an email. A place or calendar among the people is skipped. A name not in the table: ask "Who is <name>?".
- location: only the place of the event said in the event sentence (never a place mentioned only in a note, never invent one); the place name from the places table when the words are one of its spellings ("the usual place" is Café Lumen), "A -> B" (also when said "A to B") and links as said, else the words as said without "at", "in", "the". Empty if no place.
- Clash: if the event's date and times overlap an existing event (unless the request says "anyway"), ask "<event> clashes with <title> (<start> to <end>). Book anyway?".
- confirm_first: only when the request itself says to check first ("check with me first", "ask me before you put it in"), ask "Shall I put in <event> on <date>?". Never otherwise.
- desc: every sentence after the event sentence word for word, except a reminder, plus a quote inside the event sentence ("He wrote ...").
- Calendar: a keyword in an earlier event ("after my run") never decides; a note about someone ("Mum always cooks too much") is not them being there.
- reminder_minutes: only if the request says "remind": "the day before" or "a day" 1440, "N hours before" N x 60, "N minutes before" N, "a week before" 10080, any other reminder 1440. No "remind": null. Never put the reminder into desc.'''

ASK = 'If anything is unclear or contradicts itself, add "ask" to that row: the one short question, using the wording above. Ask only for the cases above, never for something the rules decide.'

WHEN_PROMPT = '''Today is {today}.
Request: {input}

These are calendar events read from the request, each with a row number:
{events}

For every row give back: row, date, end_date, allDay, start, end (and ask if needed). A row that is not an event of its own (a reminder like "the day before", a door code, a note, a bring list, or a second row for the same event) gets only {"row": n, "drop": true}. %s

%s

%s

%s

%s

Tables, settings (setting,value), existing events (title,date,start,end,calendar), durations (activity,minutes):
{knowledge}

Answer as JSON {"dated": [ ... ]}. The top-level key must be exactly "dated".''' % (ROWS, DATE_RULES, TIME_RULES, WHEN_RULES, ASK)

REPEAT_PROMPT = '''Request: {input}

These are calendar events, each with a row number:
{dated}

For every row give back: row, rrule (and ask if needed). %s

%s

%s

Answer as JSON {"repeated": [ ... ]}. The top-level key must be exactly "repeated".''' % (ROWS, REPEAT_RULES, ASK)

TITLE_PROMPT = '''These are calendar events, each with a row number:
{repeated}

The chosen calendar and people per row:
{picks}

For every row give back: row, title (and ask if needed). Build it from the row's what, never from the day, time or repeat: no weekdays, no "weekly", "daily", "annual", no dates in the title. Never write "ask" as a title. %s

%s

%s

Tables, naming (rule,pattern,when) and companies (company,short,keywords):
{knowledge}

Answer as JSON {"named": [ ... ]}. The top-level key must be exactly "named".''' % (ROWS, TITLE_RULES, ASK)

CHECK_PROMPT = '''Request: {input}

These are calendar events, each with a row number:
{named}

The chosen calendar and people per row:
{picks}

The notes per row:
{notes}

For each row give back only: row, location, attendees, calendar, desc (from the notes), reminder_minutes (and ask if needed). Title, date, times and rrule are already decided and kept for you. Attendees only from the chosen people per row, never anyone else from the people table.

%s

%s

Tables, calendars (calendar,keywords,use), people (person,also_written_as,email,calendar), existing events (title,date,start,end,calendar):
{knowledge}

Answer as JSON {"result": [ ... ]}. The top-level key must be exactly "result".''' % (CHECK_RULES, ASK)


def intake_slots(code_flow):
    """intake slots of the code flow, reused word for word"""
    p = next(s for s in code_flow['steps'] if s['id'] == 'read')['prompt']
    a = p.index('what:')
    b = p.index('Tables,')
    return p[a:b].strip()


def one_prompt_slots(code_flow):
    """no day objects here, one prompt must write real dates"""
    out, skip = [], False
    for line in intake_slots(code_flow).split('\n'):
        if line.startswith('day:'):
            skip = True
            continue
        if skip and line.startswith('  '):
            continue
        skip = False
        out.append(line)
    return '\n'.join(out)


def baseline(code_flow):
    """every rule in one instruction, like a custom agent"""
    def esc(s):   # run_baseline.py uses str.format, braces doubled
        return s.replace('{', '{{').replace('}', '}}')
    pick = next(s for s in code_flow['steps'] if s['id'] == 'pick')['prompt']
    pick_rule = pick[pick.index('calendar:'):pick.index('Calendars (')].strip()
    notes = next(s for s in code_flow['steps'] if s['id'] == 'notes')['prompt']
    notes_rule = notes[notes.index('desc:'):notes.index('Answer as JSON')].strip()
    body = '\n\n'.join([
        'You are a calendar assistant. You turn one calendar request into calendar events, following every rule below exactly. '
        'Use only the tables below for people, places, calendars, companies, naming, durations, abbreviations, misheard words, '
        'existing events and settings. Never invent anything that is not in the request or the tables.',
        'Step 1, read the request into one event per thing to put in the calendar (an instruction like "send X an invite", '
        '"remind me" or a note belongs to its event, never an event of its own). For each event note these slots:\n' + one_prompt_slots(code_flow),
        DATE_RULES, TIME_RULES, WHEN_RULES, REPEAT_RULES,
        'Calendar and people:\n' + pick_rule,
        'Notes:\n' + notes_rule.replace('reminder_minutes:', '(reminder:'),
        TITLE_RULES, CHECK_RULES,
        'Answer formats: date and end_date always YYYY-MM-DD, start and end always HH:MM (24 hours) or null, never an object, '
        'a weekday name or a word. Two-letter days in rrule (MO TU WE TH FR SA SU).',
        'Questions: whenever a rule above says ask, put that question into "asks" (one string per question) and still fill '
        'in everything you can. When nothing needs asking, "asks" is an empty list.',
    ])
    shape = ('Answer with JSON only, no other text, in exactly this shape:\n'
             '{"events": [{"title": "", "date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD", "start": "HH:MM or null", "end": "HH:MM or null", '
             '"allDay": true or false, "location": "", "attendees": [{"name": "", "email": ""}], "calendar": "", '
             '"rrule": "RRULE without the RRULE: prefix, or null", "desc": "", "reminder_minutes": number or null}], "asks": ["question"]}\n\n'
             'One object in "events" per event in the request, a repeating event is one object with an rrule.')
    check = ('Before you answer, check every event against these (4.11.26, the rules most often missed):\n'
             '1. Company: a named person, the team or a company keyword with a company in the tables puts "(<SHORT>) " in front of the title.\n'
             '2. Title words: what keeps the request\'s own words and order ("the board dinner thing" is "Board dinner", never "Dinner with the board"); '
             'each named person not already in what is added as "with <name>", even when only invited.\n'
             '3. Attendees: only people with invite = yes, people the request invites ("invite X", "send X an invite") and emails written in the request. '
             'Everyone else is in the title only, never in attendees.\n'
             '4. Two people with the same name: the one whose calendar is the event\'s calendar.\n'
             '5. Date: the weekday of the date you wrote is the weekday the request says.')
    # one prompt now handwritten in one_prompt.txt, generated text above kept for reference
    return open(os.path.join(os.path.dirname(HERE), 'one-prompt', 'baseline_prompt.txt'), encoding='utf-8').read()


# ---------- version 3, cut for a small model ----------
# one small decision per step, only its rule and table, code just merges rows

def _rule_lines(text, starts):
    return '\n'.join(l for l in text.split('\n') if any(l.startswith(s) for s in starts))


CLEAR = {
    'after': '''Does the request say "after my X" or "after the X" (X is an event, not a meal)? If not: after_end null.
If yes: in "working", list every row of the existing events table whose title matches X, with its date and times. Then take the row whose date is exactly this row's date. after_end = that row's end time (the last time in the row).
Example (made up): this row's date 2026-03-12, "after my swim", rows "Swim,2026-03-11,07:00,08:00" and "Swim,2026-03-12,18:00,19:00": working "Swim 2026-03-11 07:00-08:00; Swim 2026-03-12 18:00-19:00; date 2026-03-12 -> 19:00", after_end "19:00".''',
    'length': '''How long is the event, in minutes? Read the request. In "working", write the words you used.
1. A length said: "for 1 hour" 60, "an hour and a half" 90, "for 45 minutes" 45, "quick" 15.
2. An end time said instead ("until 5", "from 2 to 4"): minutes null (the end comes from the request).
3. Nothing said: the usual length of this row's what in the durations table (lunch 45, drinks 120, meeting 60...), else 30.
Example (made up): what "coffee", nothing said: working "coffee usual 30", minutes 30.''',
    'words': '''Each row is one event the first reading found; its what is a first guess.
- own_event: "no" if this row is not an event of its own: a reminder ("the day before"), a note, a door code, a bring list, or another name for an earlier row's event ("the report" in "the deadline thing, the report or whatever" is the same event as the deadline). Row 1 is always "yes". Else "yes".
Then write the name of the event, using the request's own words.
1. Drop request words: schedule, set up, add, put, create, book, make, block, please, can you.
2. Drop fillers: uh, uhh, finally, quick, just, maybe, "the ... thing", "or whatever".
3. Stop before "with", a day, a time, a place, "for", "every".
Fill the boxes in order:
- said: the event's name as the request says it, after rules 1 to 3: stop at the first word that is a day ("tomorrow", "next Tuesday", "the first Saturday of December"), a time, a place, a person, "with", "w/", "for", "every" or "here".
- fixes: every word of said that is in the short column of the abbreviations table or the heard column of the speech fixes table, as "short -> long". None: [].
- what: said with those fixes made, and obvious typos fixed ("meetng" -> "meeting").
Examples (made up): "Set up meetng with Tom next Friday" -> "meeting". "Add uh the budget thing, the spreadsheet or whatever, with Eva" -> "budget". "Schedule finally sort out the garden shed this weekend" -> "sort out the garden shed".
A row with own_event "no" needs nothing else filled.''',
    'people': '''Who does the request name? Write each person as the people table writes them, without the brackets.
- "my boss" or "boss" = Martin. "the team" and "the client" stay as they are.
- An email = that person's name from the people table (eva@example.com = Eva).
- "X's" (Mum's, Nat's) is a place, not the person coming along.
- Only people the request names. Nobody named: [].
Examples (made up): "drinks with Tom and Eva" -> ["Tom", "Eva"]. "train to Grandad's" -> []. "call with eva@example.com" -> ["Eva"].''',
    'company': '''Which company is the event for? Fill the boxes in order.
- client_word: copy the word "client" or "Fabrikam" if the request has it (also misspelt, like "clinet"). Else "no".
- calendar_copy: copy this row's calendar exactly.
- company: the first that fits, then stop: client_word is "client" or "Fabrikam": "FAB" (also on Work). Else calendar_copy is "Work": "NW". Else: "none".''',
    'maybe': '''Is Alex unsure? true only if the request says "maybe", "possibly" or "pencil in". Else false.''',
    'title': '''Build the title from this row's what, people, company and maybe. Fill the parts in order, then join them:
- maybe_part: maybe true gives "MAYBE ", maybe false gives "".
- company_copy: copy this row's company exactly ("none", "FAB" or "NW").
- company_part: company_copy "none" gives "". "FAB" gives "(FAB) ". "NW" gives "(NW) ". Nothing else ever goes in brackets.
- name_part: what with a capital first letter.
- people_part: people [] gives "". Else " with " and the people joined by " and ".
- title: maybe_part + company_part + name_part + people_part, exactly joined, nothing added.
Examples (made up): maybe false, company "none", what "piano lesson", people []: title "Piano lesson". maybe false, company "XY", what "planning", people ["Tom"]: title "(XY) Planning with Tom".''',
    'date': '''Which date is the event? The days table lists every date with its weekday, one row per day, in order. Fill the boxes in order.
- today: copy today's weekday and date from the top, e.g. "Saturday 2026-11-07".
- day_words: copy the request's words that say the day, e.g. "next Tuesday", "on 15 November", "in 3 days". None: "no".
- kind: pick the first that fits:
  "date" if a day of the month is written ("15 November", "the 3rd", "2026-11-16"),
  "nth" if it says "the first/second/last <weekday> of <month>",
  "weekday" if a weekday name is said, with or without next or this ("Tuesday", "next Tuesday", "this Sunday"),
  "tomorrow", "day after tomorrow", "in N days", "end of week", "end of month",
  "next week" only for the words "next week" with no weekday name,
  "none" if no day is said.
- wanted: the weekday to look for: weekday: the said weekday ("Saturday"). end of week: "Friday". next week: "Monday". Other kinds: "".
- today_row: find today's date in the days table and copy that row, e.g. "2026-11-07,Saturday".
- next_days: the 7 rows of the days table right after today_row, in order, each labelled: "in 1 day (tomorrow): Sunday 2026-11-08", "in 2 days: Monday 2026-11-09", ... up to "in 7 days: ...". Today itself is not in it.
- date:
  none: today's date, copied from the today box.
  tomorrow: the item "in 1 day (tomorrow)". day after tomorrow: the item "in 2 days".
  weekday, end of week, next week: the one of next_days whose weekday is wanted.
  in N days: the item "in N days" (in 3 days: the item "in 3 days"). More than 7 days: N rows below today_row in the days table.
  date: the written day and month, no counting. The year is today's year, the next year only if that day has passed.
  nth: in that month in the days table, the first row with that weekday (second: the second such row, last: the last).
  end of month: the last row of this month.''',
    'start': '''When does the event start? Fill the boxes in order.
- time_words: copy the request's words that say the time, e.g. "at half 3", "from 10 to 11", "in the afternoon", "after my swim", "all day", "keep the whole morning free". Not the day. None: "".
- time_kind: from time_words, the first that fits:
  "two times" (two different times are said: "all day" and a single clock time ("all day, at 4"), "at noon" and "from 2 to 5", "after lunch" and "for the morning"; one range like "from 10 to 11" is one time; "all day" with a range ("between 6 and 9 pm all day") is "clock", the range wins),
  "all day" (all day, the whole day),
  "half day" (for the morning, keep the whole morning free, the rest of the afternoon),
  "clock" (a number or noon: at 4, 3 pm, 08:30, half 10, noon, from 10 to 11, between 6 and 7),
  "soft" (early, in the morning, late morning, lunchtime, after lunch, in the afternoon, in the evening, after dinner),
  "after event" only if time_words has the words "after my" or "after the" ("after my standup", "after the review"),
  "none".
- event_row: only for after event: copy the row of the existing events table whose title is that event and whose date is this row's date, e.g. "Swim,2026-03-12,18:00,19:00,Sport". Other kinds: "".
- start:
  two times: null.
  all day: null.
  half day: morning 08:00, afternoon 12:00, evening 18:00. "the rest of the <part>": the time now, copied from the "Today is" line.
  after event: the end time in event_row, the time just before the calendar name. "Swim,2026-03-12,18:00,19:00,Sport" gives 19:00.
  clock: the first time in time_words, as 24-hour HH:MM. "3 pm" 15:00, "12:30 pm" 12:30, "9 am" 09:00, "08:30" 08:30, "half 10" 10:30, "noon" 12:00. Without am or pm: an hour 1 to 7 is pm; 8 to 12 stays as it is.
  pm hours: 1 is 13, 2 is 14, 3 is 15, 4 is 16, 5 is 17, 6 is 18, 7 is 19 ("half 5" 17:30).
  soft: early 08:00, but 11:30 if what is a lunch; in the morning 09:00, late morning 11:00, lunchtime 12:00, after lunch 13:30, in the afternoon 14:00, in the evening 19:00, after dinner 20:00.
  none: null.
- allDay: true only for all day.
- ask: two times: one short question naming both, e.g. "All day, or from 18:00?". Else "".''',
    'end': '''When does the event end? This row's start is already decided. Fill the boxes in order.
- from: copy this row's start (null if allDay is true).
- end_words: copy the request's words that say the end or the length, e.g. "until 6 pm", "from 10 to 11", "for 50 minutes", "an hour and a half", "quick", "for the morning". None: "".
- end_kind: the first that fits:
  "end said" (end_words has the word until, till or to, or "between ... and ..."),
  "all day" (allDay is true in this row),
  "half day" (for the morning, keep the whole morning free, the rest of the afternoon),
  "quick" (end_words is "quick"),
  "length" (a length is said: for 1 hour, an hour and a half, for 45 minutes),
  "usual" (nothing said).
- minutes: quick: 15. length: from end_words (1 hour 60, an hour and a half 90, 45 minutes 45). usual: the number for this row's what in the durations table, else 30. all day, end said, half day: 0.
- add: for quick, length and usual, minutes as hours:minutes (15 = 0:15, 45 = 0:45, 60 = 1:00, 90 = 1:30, 120 = 2:00). Else "".
- end:
  end said: the end time in end_words, as 24-hour HH:MM (without am or pm, an hour 1 to 7 is pm: "until 6" 18:00, "to 11" 11:00). An end that would be before from is pm: from 20:00, "until 11" is 23:00.
  half day: morning 12:00, afternoon 18:00, evening 22:00.
  quick, length and usual: from + add. Add the hours to from's hour, then the minutes to from's minutes; if the minutes reach 60 or more, take 60 off and add 1 to the hour.
  Examples (made up): from 16:40, add 0:50: hour 16 + 0 = 16, minutes 40 + 50 = 90, 90 - 60 = 30 so hour 17: end 17:30. from 09:10, add 0:15: hour 9 + 0 = 9, minutes 10 + 15 = 25, under 60 so the hour stays: end 09:25.
  All day: null.
- end_date: this row's date (the next day only if the end passes midnight).''',
    'repeat': '''Does the event repeat? Most events happen once. Fill the boxes in order.
- repeat_words: copy the request's repeat words (every, each, daily, weekly, monthly, yearly, fortnightly, "for N weeks", "Monday to Friday"), e.g. "every Tuesday". None: "no".
- until_words: copy what comes after "until" when it ends the repeat, e.g. "until Christmas" gives "Christmas". "until 11" or "until 5 pm" is the end time of the event, not of the repeat: "no". None: "no".
- until_date: the date of until_words as YYYYMMDD. Christmas is 20261225. The end of the year is 20261231. until_words "no": "no".
- repeat_kind: from repeat_words, the first that fits: "none" (repeat_words "no"), "daily" (every day, every morning, daily), "weekdays" (every weekday, Monday to Friday), "every other week" (every other <weekday>, fortnightly), "weekly" (every <weekday>, weekly, every week), "monthly date" (every month, every month on that date), "monthly weekday" (on the same weekday every month), "yearly" (every year), "count" (for N weeks).
- freq: none: "". daily: FREQ=DAILY. weekdays: FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR. every other week: FREQ=WEEKLY;INTERVAL=2;BYDAY= followed by the weekday's two letters. weekly: FREQ=WEEKLY;BYDAY= followed by the weekday's two letters (every Saturday: FREQ=WEEKLY;BYDAY=SA). monthly date: FREQ=MONTHLY;BYMONTHDAY=<day of this row's date>. monthly weekday: FREQ=MONTHLY;BYDAY=<which one in the month><two letters> (the 4th Thursday is 4TH, the last Monday -1MO). yearly: FREQ=YEARLY. count: FREQ=WEEKLY;COUNT=<N>.
  Two letters: MO TU WE TH FR SA SU.
- until_part: if until_date is "no": "". Otherwise ";UNTIL=" and until_date.
- rrule: freq + until_part, exactly joined (freq "" and until_part "" give "").
Examples (made up): "every Wednesday until Easter": repeat_words "every Wednesday", until_words "Easter", until_date <Easter>, repeat_kind "weekly", freq "FREQ=WEEKLY;BYDAY=WE", until_part ";UNTIL=<Easter>", rrule "FREQ=WEEKLY;BYDAY=WE;UNTIL=<Easter>". "piano lesson on Friday": repeat_words "no", until_words "no", until_date "no", repeat_kind "none", freq "", until_part "", rrule "".''',
    'place': '''Where is the event? Fill the boxes in order. Use only the first sentence of the request.
- place_words: copy the words after "at", "in", "from", "on" or "here" that name where the event is (a place, a room, a street, a city, a link, Teams), with that first word. Not a time ("at 5", "in the afternoon"). None: "no".
- table_row: the row of the places table whose place or one of its also_written_as spellings is in the first sentence of the request, copied whole. No match: "".
- place_kind: the first that fits: "table" (table_row is not "": always table, never address), "link" (place_words has http), "teams" (on Teams), "from to" (from A to B), "address" (a street address or a room), "city" (a city), "none" (place_words "no").
- location:
  table: the text before the first comma of table_row ("<name>,<other spellings>," gives "<name>").
  link: the link, copied exactly.
  teams: Microsoft Teams.
  from to: "A -> B" ("from <city one> to <city two>" gives "<city one> -> <city two>").
  address: as said, without at or in ("<number> <street>", "room <number>").
  city: the city.
  none: "".''',
    'calendar': '''Which calendar? Check in this order and stop at the first that fits:
1. A trip whose point is the journey (train, flight, drive, commute from A to B): Travelling Buffer.
2. With Martin or for the client: Work.
3. Sport or exercise (run, marathon, bike ride, climbing, training, skating, yoga, gym) with friends (Sam, Natalie, Arthur, Jordan as a friend): Friends.
4. An activity keyword from the calendars table in the event's name (gym, physio, dentist, lecture, thesis, study, therapy...): that calendar. Physio, therapy, braces and the dentist are Me Time, also with Mum or after a standup: Alex wears braces, so a braces check is a personal health appointment, never work.
5. The calendar of the people named (people table).
6. At a family member's home (Mum's): Family.
7. Otherwise: IU.
Examples (made up): "tennis with a friend" -> Friends. "swimming alone" -> Self-Improvement. "dinner with the boss and a friend" -> Work.''',
    'invite': '''Who gets an invitation? Fill the boxes in order. Look only at the people in this row.
- calendar_copy: copy this row's calendar exactly.
- is_work: true if calendar_copy is "Work", else false.
- row_people: copy this row's people list exactly.
- on_list: is_work false: []. is_work true: the names of row_people that are also in the invites table, written as there; a name not in row_people never. Nobody: [].
- invite_words: copy the request's words that invite someone ("invite Tom", "send Tom an invite") or an email address in it. Most requests have none: "no".
- asked: if invite_words is "no": []. Otherwise: the people named in invite_words.
- attendees: one item for each name in on_list, plus one for each name or email in asked, each as {"name": name, "email": the email column (the one with @) of the invites table, else of the people table}. For an email in asked: the person with that email. on_list and asked both []: [].''',
    'notes': '''Notes. Fill the boxes in order.
- sentences: the whole request cut into its sentences, a new item after every ". ", each copied exactly to its full stop (commas stay inside; a quote mark inside a sentence is written as '), as a list.
- later: sentences without its first item, and without a sentence starting "Remind me". As a list.
- desc: the items of later joined with a space. later empty: a quote inside the first sentence, else "". Never reword.
- reminder_minutes: only if the request says "remind": the day before 1440, an hour before 60, a week before 10080; else null.''',
    'check': '''Does the event clash with an existing event? Fill the boxes in order.
- date_copy: copy this row's date exactly.
- same_day: the rows of the existing events table (title,date,start,end,calendar) whose date is exactly date_copy, each row copied whole as one item. This event itself is not in that table. Rows with any other date never. None: [].
- this_slots: the quarter hours this event covers, from its start up to its end, not the end itself: start 14:40, end 15:10 gives ["14:30", "14:45", "15:00"] (a quarter hour counts if the event is on during it). All day or no start: [].
- other_slots: for each same_day row, "<title>: " and the quarter hours it covers the same way. None: [].
- shared: the quarter hours that are in this_slots and also in one of other_slots, with that title: "Swim 14:45". None: [].
- clash_with: shared not empty: the title in shared. Else "". An event that starts exactly when the other one ends ("after my <event>") is not a clash.
- confirm_first: true if the request says to check with Alex first, else false.
- need_ask: true if clash_with is not "" or confirm_first is true. Else false.
- ask: need_ask true: one short question, e.g. "This clashes with <clash_with>. Book it anyway?". need_ask false: "".''',
}
WORKING = {'notes'}


def native_steps(code):
    intake = next(s for s in code['steps'] if s['id'] == 'read')
    pick = next(s for s in code['steps'] if s['id'] == 'pick')['prompt']
    notes_rule = next(s for s in code['steps'] if s['id'] == 'notes')['prompt']
    cal_rule = pick[pick.index('calendar:'):pick.index('people:')].strip()
    people_rule = pick[pick.index('people:'):pick.index('Calendars (')].strip()
    title_lines = TITLE_RULES.split('\n')
    what_rule = next(l for l in title_lines if l.startswith('- What:'))
    who_rule = next(l for l in title_lines if l.startswith('- Who:'))
    check_lines = CHECK_RULES.split('\n')
    loc_rule = next(l for l in check_lines if l.startswith('- location:'))
    inv_rules = '\n'.join(l for l in check_lines if l.startswith(('- People:', '- Invitations:', '- attendees:')))
    desc_rules = '\n'.join(l for l in check_lines if l.startswith(('- desc:', '- reminder_minutes:')))
    when_lines = WHEN_RULES.split('\n')
    start_rules = '\n'.join(l for l in when_lines if not l.startswith(('- End:', '- end_date', '- An end earlier', '- No start')))
    end_rules = '\n'.join(l for l in when_lines if l.startswith(('- End:', '- end_date', '- An end earlier')))

    spec = [   # id, title, out fields, rules, tables, needs
        ('words', 'What is the event?', ['own_event', 'said', 'fixes', 'what'],
         'What is the event, in the request\'s own words? Only the few words that name the event: stop before "with", "at", "on", "in", "for", "from", "every", a day or a time. Examples: "Set up meetign with Jordan next Tuesday from 10 to 11" gives "meeting"; "Schedule finally go through the thesis results with fresh eyes" gives "go through the thesis results"; "Add uh the deadline thing, the report or whatever" gives "deadline".\n' + what_rule + '\n- A row that is not an event of its own (a reminder like "the day before", a door code, a note, a bring list, or a second row for the same event) gets only {"row": n, "drop": true}.',
         ['thesis/speech-fixes', 'thesis/abbreviations'], {}),
        ('people', 'Who is it with?', ['people'],
         'Who is the event with? A list of the people the request names, each as the people table writes it without brackets, or [] for nobody.\n' + who_rule,
         ['thesis/people'], {}),
        ('maybe', 'Is Alex unsure?', ['maybe'],
         'Is Alex unsure about the event ("maybe", "possibly", "pencil in")? true or false.', [], {}),
        ('date', 'Which day?', ['today', 'day_words', 'kind', 'wanted', 'today_row', 'next_days', 'date'],
         DATE_RULES, ['thesis/days'], {'date': 'required'}),
        ('start', 'What time does it start?', ['time_words', 'time_kind', 'event_row', 'start', 'allDay', 'ask'],
         'The start time as "HH:MM" (or null) and allDay (true or false).\n' + TIME_RULES + '\n' + start_rules,
         ['thesis/settings', 'thesis/existing-events'], {'start': 'required_unless_all_day'}),
        ('end', 'When does it end?', ['from', 'end_words', 'end_kind', 'minutes', 'add', 'end', 'end_date'],
         'The end time as "HH:MM" (or null when all day) and end_date (YYYY-MM-DD).\n' + end_rules,
         ['thesis/durations'], {'end': 'required_unless_all_day'}),
        ('repeat', 'How often?', ['repeat_words', 'until_words', 'until_date', 'repeat_kind', 'freq', 'until_part', 'rrule'], REPEAT_RULES, [], {}),
        ('place', 'Where?', ['place_words', 'table_row', 'place_kind', 'location'], 'Where is the event?\n' + loc_rule, ['thesis/places'], {}),
        ('calendar', 'Which calendar?', ['calendar'], 'Which calendar?\n' + cal_rule.replace('calendar: ', '', 1), ['thesis/calendars', 'thesis/people'], {'calendar': 'required'}),
        ('company', 'For which company?', ['client_word', 'calendar_copy', 'company'],
         'Is the event for a company? Go through the keywords column of the companies table: is one of those words in the request? If none is, answer "" (most events, like gym, lunch with a friend or a family dinner, are for no company). If "client" or "Fabrikam" is in the request, "FAB". Else if one of Northwind\'s keywords is in it (Martin, my boss, boss, the team, team, standup, course prep, internship), or a person in this row works for Northwind (the company column of the people table, e.g. Jordan (work)), "NW".',
         [], {}),
        ('title', 'What is it called?', ['maybe_part', 'company_copy', 'company_part', 'name_part', 'people_part', 'title'],
         'Put the title together from this row\'s what, people, company and maybe, in exactly this order, left to right:\n1. "MAYBE " only if maybe is true\n2. "(<company>) " only if company is not empty\n3. what, capital first letter\n4. " with " and the people joined by " and ", only if people is not empty and the person is not already in what\nExamples: maybe false, company "", what "gym", people [] gives "Gym". maybe true, company "FAB", what "lunch", people ["the client"] gives "MAYBE (FAB) Lunch with the client". maybe false, company "NW", what "standup", people ["Martin"] gives "(NW) Standup with Martin". Nothing else in the title.',
         [], {'title': 'required'}),
        ('invite', 'Who gets an invitation?', ['calendar_copy', 'is_work', 'row_people', 'on_list', 'invite_words', 'asked', 'attendees'],
         'Who gets an invitation? attendees: a list of {"name", "email"}, or [].\n' + inv_rules, ['thesis/invites', 'thesis/people'], {}),
        ('notes', 'Anything for the notes?', ['desc', 'reminder_minutes'],
         desc_rules + '\n' + notes_rule[notes_rule.index('desc:'):notes_rule.index('Answer as JSON')].strip(), [], {}),
        ('check', 'Anything to ask?', ['date_copy', 'same_day', 'this_slots', 'other_slots', 'shared', 'clash_with', 'confirm_first', 'need_ask', 'ask'],
         'Does the request contradict itself, or does the event clash with an existing event? If yes, "ask": the one short question; else "". Ask only for these:\n'
         + _rule_lines(WHEN_RULES, ('- "all day"', '- Two different times', '- "at <time>"')) + '\n'
         + _rule_lines(CHECK_RULES, ('- Clash:', '- confirm_first')),
         ['thesis/existing-events'], {}),
    ]
    steps = [dict(intake)]
    prev = 'events'
    for i, (sid, title, fields, rules, uses, needs) in enumerate(spec):
        rules = CLEAR.get(sid, rules)   # short clear version of the same rules
        if set(fields) & {'start', 'end', 'from'}:   # else qwen puts the date in and the time pattern cuts it wrong
            rules += '\nTimes are only hours and minutes, like "13:30", never with the date.'
        if 'date' in fields:   # else qwen starts day first and the pattern mangles it
            rules += '\nThe date is written year first: <year>-<month>-<day>, never the day first.'
        if sid in WORKING:
            fields = ['working'] + fields
        out = 'result' if sid == 'check' else 'r_' + sid
        shape = '{"%s": [{"row": 1, %s}]}' % (out, ', '.join('"%s": ...' % f for f in fields))
        prompt = ('Today is {today}.\nRequest: {input}\n\nThe events so far, one row each:\n{%s}\n\n%s\n\nFor every row give back only "row" and %s, nothing else; '
                  'the other fields are kept for you.%s\n\nAnswer as JSON %s. The top-level key must be exactly "%s".') % (
            prev, rules, ', '.join('"%s"' % f for f in fields), '\n\nTables:\n{knowledge}' if uses else '', shape, out)
        steps.append({'id': sid, 'title': title, 'kind': 'model', 'in': prev, 'out': out, 'merge': prev, 'uses': uses,
                      'reads': ['input', 'today'], 'asks': sid in ('words', 'date', 'start', 'check'), 'needs': needs,
                      'shows': fields, 'prompt': prompt, 'may_be_empty': True})   # nothing to add, row carries on
        prev = out
    return steps


CALENDARS = None
VIEWS = {'words': ['what'], 'people': [], 'company': ['what', 'people', 'calendar'], 'maybe': [],   # later steps see only earlier v3 decisions, never intake guesses
         'title': ['what', 'people', 'company', 'maybe'], 'date': [],
         'start': ['what', 'date'], 'end': ['what', 'date', 'start', 'allDay'], 'repeat': ['date'], 'place': [],
         'calendar': ['what', 'people'], 'invite': ['people', 'calendar'], 'notes': [], 'check': ['what', 'date', 'start', 'end', 'allDay']}
DATE = {'type': 'string', 'pattern': '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'}
TIME = {'type': ['string', 'null'], 'pattern': '^([01][0-9]|2[0-3]):[0-5][0-9]$'}


def field_schema(f):
    import csv
    if f in ('date', 'end_date'):
        return DATE
    if f in ('start', 'end'):
        return TIME
    if f in ('allDay', 'maybe', 'drop', 'is_work', 'two_times', 'confirm_first', 'need_ask', 'client', 'two_times'):
        return {'type': 'boolean'}
    if f == 'company':
        return {'type': 'string', 'enum': ['none', 'FAB', 'NW']}   # qwen won't write empty, "" turned into NW
    if f == 'calendar':
        names = [r['calendar'] for r in csv.DictReader(open(os.path.join(os.path.dirname(os.path.dirname(CODE_FLOW)), 'knowledge', 'thesis', 'calendars.csv'), encoding='utf-8-sig'))]
        return {'type': 'string', 'enum': names}
    if f == 'from':
        return TIME
    if f in ('on_list', 'asked', 'fixes', 'sentences', 'later', 'times_said', 'same_day', 'row_people', 'compare', 'this_slots', 'other_slots', 'shared', 'month_start'):
        return {'type': 'array', 'items': {'type': 'string'}}
    if f == 'repeat_kind':
        return {'type': 'string', 'enum': ['none', 'daily', 'weekdays', 'every other week', 'weekly', 'monthly date', 'monthly weekday', 'yearly', 'count']}
    if f == 'own_event':
        return {'type': 'string', 'enum': ['yes', 'no']}
    if f == 'place_kind':
        return {'type': 'string', 'enum': ['table', 'link', 'teams', 'from to', 'address', 'city', 'none']}
    if f == 'end_kind':
        return {'type': 'string', 'enum': ['all day', 'end said', 'half day', 'quick', 'length', 'usual']}
    if f == 'time_kind':
        return {'type': 'string', 'enum': ['two times', 'all day', 'half day', 'after event', 'clock', 'soft', 'none']}
    if f == 'people':
        return {'type': 'array', 'items': {'type': 'string'}}
    if f == 'next_days':
        return {'type': 'array', 'items': {'type': 'string'}, 'minItems': 7, 'maxItems': 7}
    if f == 'first_week':
        return {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 7}
    if f == 'attendees':
        return {'type': 'array', 'items': {'type': 'object', 'properties': {'name': {'type': 'string'}, 'email': {'type': 'string'}}, 'required': ['name', 'email']}}
    if f in ('days_to_add', 'today_number', 'wanted_number'):
        return {'type': 'integer'}
    if f == 'kind':
        return {'type': 'string', 'enum': ['none', 'tomorrow', 'day after tomorrow', 'in N days', 'weekday', 'date', 'nth', 'end of week', 'next week', 'end of month']}
    if f == 'after_end':
        return TIME
    if f == 'minute_sum':
        return {'type': 'integer'}
    if f == 'end_hour':
        return {'type': 'integer'}
    if f == 'minutes':
        return {'type': ['integer', 'null']}
    if f == 'reminder_minutes':
        return {'type': ['integer', 'null']}
    if f == 'rrule':
        return {'type': ['string', 'null']}
    return {'type': 'string'}


def step_schema(out, fields):
    props = {'row': {'type': 'integer'}}
    props.update({f: field_schema(f) for f in fields})   # working first so model writes it before the answer
    props.update({'ask': {'type': 'string'}, 'drop': {'type': 'boolean'}})
    return {'type': 'object', 'properties': {out: {'type': 'array', 'minItems': 1,   # never empty, every step answers each row
                                                   'items': {'type': 'object', 'properties': props, 'required': ['row'] + list(fields)}}},
            'required': [out]}


def baseline_schema():
    """one-prompt answer shape for structured outputs"""
    # people first so model copies their rows before title, calendar, attendees
    ev = {'people': {'type': 'array', 'items': {'type': 'object', 'properties': {'said': {'type': 'string'}, 'row': {'type': 'string'}},
                                                 'required': ['said', 'row']}}}
    ev.update({f: field_schema(f) for f in ('title', 'date', 'end_date', 'start', 'end', 'allDay', 'location', 'attendees', 'calendar', 'rrule', 'desc', 'reminder_minutes')})
    ev['location'] = {'type': ['string', 'null']}
    ev['desc'] = {'type': ['string', 'null']}
    return {'type': 'object', 'properties': {'events': {'type': 'array', 'items': {'type': 'object', 'properties': ev, 'required': list(ev)}},
                                             'asks': {'type': 'array', 'items': {'type': 'string'}}}, 'required': ['events', 'asks']}


def build():
    code = json.load(open(CODE_FLOW, encoding='utf-8'))
    allm = json.load(open(ALL_FLOW, encoding='utf-8'))
    tail = [x for x in allm['steps'] if x['id'] in ('ask', 'create')]
    allm['steps'] = native_steps(code) + tail   # v3 steps then the same ask and create steps
    for x in tail:
        x['after'] = ['check']
    allm['description'] = ('the thesis test version of create events, all-model condition: cut for a small model, one small decision per step '
                           '(14 model steps), each with only its rule and table; plain code only carries the rows (1.11.26)')
    new = {'when': WHEN_PROMPT, 'repeat': REPEAT_PROMPT, 'title': TITLE_PROMPT, 'check': CHECK_PROMPT}
    same = {s['id']: s['prompt'] for s in code['steps'] if s['kind'] == 'model'}   # read, pick, notes: identical in both flows
    for s in allm['steps']:
        if s['id'] in new and 'r_' not in str(s.get('out')) and s['id'] != 'check':
            s['prompt'] = new[s['id']]
        elif s['id'] in same and ('r_' not in str(s.get('out')) or s['id'] == 'notes'):
            s['prompt'] = same[s['id']]
        if s['id'] in ('when', 'repeat', 'check'):
            s['reads'] = sorted(set(s.get('reads') or []) | {'input'})
        merge = {'when': 'events', 'repeat': 'dated', 'title': 'repeated', 'check': 'named'}   # answers only its own fields
        if s['id'] in merge:
            s['merge'] = merge[s['id']]
    for x in allm['steps']:   # v3 steps merge into the step before
        if x.get('kind') == 'model' and x.get('in'):
            x['merge'] = x['in']
        if x['id'] in VIEWS:   # only needed fields in, fixed shape out
            x['view'] = VIEWS[x['id']]
            x['schema'] = step_schema(x['out'], x['shows'])
    return allm, baseline(code)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true')
    a = ap.parse_args()
    allm, base = build()
    allm_txt = json.dumps(allm, ensure_ascii=False, indent=2) + '\n'
    old_allm = open(ALL_FLOW, encoding='utf-8').read()
    old_base = open(BASELINE, encoding='utf-8').read()
    if a.check:
        sys.exit(0 if (old_allm == allm_txt and old_base == base) else 1)
    open(ALL_FLOW, 'w', encoding='utf-8', newline='\n').write(allm_txt)
    open(BASELINE, 'w', encoding='utf-8', newline='\n').write(base)
    json.dump(baseline_schema(), open(os.path.join(os.path.dirname(HERE), 'one-prompt', 'baseline_schema.json'), 'w', encoding='utf-8'), indent=1)
    print('all-model flow:', 'changed' if old_allm != allm_txt else 'same', '| baseline prompt:', 'changed' if old_base != base else 'same',
          '| baseline length', len(base), 'chars')


if __name__ == '__main__':
    main()
