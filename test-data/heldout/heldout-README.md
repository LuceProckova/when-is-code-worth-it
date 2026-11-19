# Held-out calendar test requests

**Written:** 30.10.26

**Status:** sealed. Don't open, run or tune anything against `heldout-requests.csv` until all the main test runs on `requests.csv` are finished. It's run once, at the end, to check that results hold on requests nobody has tuned the app or its tables to.

**How they were made**
- 40 rows, ids H1 to H40, same columns as `requests.csv` (id, today, codes, ask, request, sources).
- Written by an agent that only saw the blueprint in `Test-set.html` (9 fields x 10 options plus Today W1 to W10) and 5 example rows of `requests.csv`. It never read the app's code or its tables (settings, places, people, naming, durations, calendars).
- Codes were drawn with a seeded shuffle so every option of Today, Date, Start, Duration, Title, Location, People, Description and Calendar appears exactly 4 times. Repeat: R1 (once) 20 times, the other 20 spread over R2 to R10 (2 or 3 each).
- The request text was then written by hand to match each code line, with new first names, made-up places and situations, typos, abbreviations and speech-to-text slips. Every date is in the future relative to that row's Today.
- 6 rows contradict themselves on purpose (ask = yes): H8, H17, H21, H27, H34, H38. The right answer for those is to ask the person.
- Privacy: no real people, companies or addresses. Companies are only Northwind or Fabrikam, emails and links use example.com, phone numbers use the fictional +44 20 7946 range.
