# Scoring rules (analyse.py)

## Unit
- One score per request per condition, from the **modal output** across repeats (the result JSON compared after key sorting). Stability = share of repeats equal to the modal output.
- Energy, time and tokens per request = mean across repeats.

## Ask rows and asks
- **Contradiction row** = gold `ask` column not empty. No field is scored. The row (`event`) is correct if the output has at least one ask and that ask mentions time ("start", "time", "morning", "all day", "or").
- **Clash ask** (1.11.26): a gold start of `ask` for an overlap with an existing event is also correct when the ask says "clash" or "book anyway".
- **Field ask** = a gold field value of `ask` (for example calendar or attendees). That field is correct if an ask mentions it: calendar = "calendar", attendees = "which", "who", "attendee", "invite", date = "day"/"date", end = "end"/"how long"/"length", location = "where"/"location".
- **Should ask** = contradiction row or any field ask. **asked** = output has any ask. `ask_correct` = asked exactly when it should. False ask = asked when it should not, missed ask = did not ask when it should.

## Fields (non-ask rows; no event or more than one event = every field wrong)
- **date, start, end, calendar**: exact string match (ISO date, HH:MM, calendar name). Empty and missing are equal.
- **all_day**: exact boolean; empty, null and FALSE all mean false.
- **location**: NFKC, casefold, punctuation removed (keeps `()`, `>`, `-` so "A -> B" trips survive), whitespace collapsed, then equal. A leading "in", "at", "the" or "room" is ignored and "A to B" equals "A -> B" (1.11.26).
- **attendees**: set equality on email addresses, case-insensitive, order ignored. A person with no email counts by name.
- **title** (changed 1.11.26, people counted across the whole title so "Team lunch" = "Lunch with the team"): the rule-governed parts must match exactly, the prefix (MAYBE, (NW)/(FAB), TEMP - X -, PH - X -) and the set of people after "with". The event words are free wording, so they count as right when the content words of one title (stop words like the, that, have, do, get removed, first 5 letters compared) contain the other's: "Long breakfast" = "Have that long breakfast", but "Fix the report" is not "Go through the thesis results". An exact match after normalising (casefold, punctuation removed, leading verb removed) is always right.
- **rrule**: both rules expanded with dateutil from the gold start (gold date + start) over the next year; equal if the occurrence lists are identical. A date-only UNTIL is read as 23:59:59 that day. Both empty = correct, one empty = wrong. Unparseable rule = case-insensitive string match. Three-letter day names (THU) count as their two-letter code (TH), same meaning (1.11.26).
- **description**: correct if every key item in the gold description is present in the output: links (http...), numbers and phone numbers (spaces ignored), and codes (2+ capital letters, optional digits). Wording is ignored and extra text is not penalised. A gold description with no key items is always correct.
- **event** (whole event): every scored field correct and no ask unless one was expected.

## Statistics
- Accuracy CIs: Wilson 95%.
- Two conditions (first two in sorted order, A and B): exact McNemar per field (binomial test on discordant pairs), Holm across fields + ask_correct; `event` is primary and not Holm-adjusted here.
- Energy, time, tokens: Wilcoxon signed-rank on per-request means, geometric mean ratio B/A with a paired bootstrap 95% CI (10,000 resamples of requests, seed 20261030).
- Per option: codes column of 10-Test-Data/requests.csv; letters as defined in 10-Test-Data/01-Test-set-blueprint.html section 1, each mapped to the field it is scored on: W today and D date to date, S start time to start, L duration (length) to end, T title, P location (place), A people (attendees), N description (notes), C calendar, R repeat to rrule. R2 to R10 pooled as "R2-R10". Descriptive only, Wilson CIs, no tests.
