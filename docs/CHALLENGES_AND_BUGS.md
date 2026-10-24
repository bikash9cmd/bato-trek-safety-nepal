# Challenges and bugs

What went wrong while building this, roughly in the order I hit it, with the evidence and the fix. Most were found by running the code on real data or through the season simulation; a few were found by a test, and two by the template linter. I've said which.

## Data and measurement

### 1. The data sources I planned on weren't reachable

**Symptom:** the ERA5 bucket I knew (`era5-pds`) returned `AccessDenied` for every key, and the Open-Meteo API I meant to use for both forecasts and history wasn't reachable from the network I was building on, which only allowed public AWS buckets.

**What I did:** looked for the same data on AWS Open Data and found two better sources: the **NSF NCAR copy of ERA5** (`nsf-ncar-era5`) and NOAA's **GEFSv12 reforecast** (`noaa-gefs-retrospective`), 20 years of real forecasts in the same format as the live feed. **Lesson:** this changed the project for the better. With Open-Meteo history I could only have said "it snowed a lot on 14 October"; with the reforecast I can say what the forecast said the morning *before*, which is the question a hold rule has to answer.

### 2. A point read of ERA5 downloads the whole world

**Symptom:** reading one grid cell from a half-month ERA5 file took as long as downloading it.

**Cause:** the NetCDF4 files are chunked one global forecast field per chunk (12 × 721 × 1440), so any read of any point decompresses every chunk. 100 files was ~16 GB streamed. My first version used a thread pool and still ran at about one file every 3 minutes, because h5py holds a global lock: threads never decompressed in parallel.

**Fix:** a process pool, and a per-file CSV cache in `data/raw/era5/`. The cache paid for itself the same day: the run died at 75 of 100 files when my session was interrupted, and the re-run fetched only the missing 25. **Lesson:** for any long download, make every unit of work resumable before starting.

### 3. Decoding the same GRIB field six times

**Symptom:** pulling 1,530 reforecasts took about an hour of CPU, not of network.

**Cause:** for each 6-hour message I called ecCodes' `codes_grib_find_nearest` once per pass, and each call decoded the full 1M-point field again. Six passes, six decodes.

**Fix:** in the live reader (`bato/forecast.py`) the field is decoded once and each pass is a direct index into the 0.25° grid (`grid_index`, with a test that pins Thorong La to row 245, column 336). I also fetch only the APCP messages by byte range from the `.idx` file: about 3 MB per forecast instead of 25 MB. The one-off archive script still does it the slow way; it ran once and I left it.

### 4. The simulation said accidents were found 44 hours before they happened

**Symptom:** the first run of the season simulation reported a median time from accident to first alert of **-44.1 hours**.

**Cause:** I recorded the first alert ever raised for a group, and many groups had a false alarm days before their (simulated) accident. The metric was measuring the wrong alarm.

**Fix:** only alerts raised at or after the accident count towards detection; earlier ones count as false alarms. **Lesson:** a negative duration is a gift; the subtler version of this bug (a wrong but positive number) would have gone straight into the README.

### 5. Laurebina La never snows

**Symptom:** in ten years of ERA5, the Laurebina La cell (4,610 m pass, Gosainkunda) never once has 20 mm of snow in a day; its wettest day is under 10 mm. Every hold there scores as a false alarm.

**Cause:** a 25 km reanalysis cell averages the pass with the deep valleys around it. The cell's mean elevation is far below 4,610 m, so the model often has rain or nothing where the pass gets snow, and it smooths out local heavy falls. GEFS forecasts precipitation there, ERA5 doesn't call it snow, and the two disagree.

**What I did:** reported it rather than tuning it away: the per-pass table in [STORM_HOLDS.md](STORM_HOLDS.md) shows 0 dangerous days and 14 holds. **Lesson:** a reanalysis is a yardstick for *which days* were bad, not a measurement on the trail. The real fix is a season of wardens' observations.

### 6. Three passes, one grid cell

**Symptom:** the first hindcast reported 54 dangerous days and identical scores for Cho La, Renjo La and Kongma La.

**Cause:** all three are within about 20 km and fall in the same 0.25° cell in both GEFS and ERA5. Every Khumbu storm was counted three times, which inflated the totals and made the rule look better tested than it was.

**Fix:** score per grid cell (`hindcast.cell_of`): 4 cells, 30 dangerous days. **Test:** `test_hold_threshold_catches_most_dangerous_days_with_about_one_hold_a_season` asserts 4 cells. **Lesson:** independent-looking rows aren't independent if they share an input.

## The engine

### 7. A Kathmandu landline became a US number

**Symptom:** in the first end-to-end run the agency's number `01-4400000` was stored as `+14400000`.

**Cause:** my normaliser stripped the trunk `0` and then treated anything that wasn't a 10-digit mobile as already international. `14400000` with a `+` is a North American number.

**Fix:** a number is foreign only if it was written with `+` or `00`; everything else is Nepali, mobile or landline, and gets `+977`. Devanagari digits are converted first. **Test:** `test_phone_numbers_as_written_in_nepal`. Every L2 and L3 message goes to that number; this bug would have sent rescue cases to nowhere.

### 8. "Phedi" is two places

**Symptom:** `IN phedi` resolved to Thorong Phedi for a group on the Gosainkunda route.

**Cause:** *phedi* means the foot of a climb; there's one below Thorong La and one below Laurebina La, and guides shorten both to "Phedi".

**Fix:** the name index maps a name to a list of stops; the group's own itinerary wins, and an ambiguous name with no itinerary context returns nothing rather than a guess. **Test:** `test_phedi_means_the_phedi_on_your_own_route`.

### 9. An unprompted OK could shorten a deadline

**Symptom:** caught while writing `ok()`: a guide replying `OK` at 08:00, before anything was overdue, set the deadline to 12:00, earlier than the 15:30 the plan allowed.

**Fix:** an OK that isn't answering an alarm keeps the later of the two deadlines.

### 10. Slow groups were alarmed every afternoon

**Symptom:** tracing one simulated Everest group showed an L1 every single day: 13:00, 14:00, 15:00, 18:30...

**Cause:** the group was about 40% slower than the book times, every day. A deadline built from book times is wrong for them in the same direction every day.

**Fix:** pace learning. Each same-day arrival nudges the group's pace toward walked/book hours (weight 0.4, clamped to 0.9-1.8x), and deadlines scale with it. Readings outside 0.5-2.5x are ignored (a message sent hours late). Because the check-in time is an upper bound on the arrival time, the error is always towards more time, never less. **Test:** `test_pace_is_learned_and_clamped`. Found by the simulation.

### 11. "OK" at 7 pm meant a rescue case at 5 am

**Symptom:** a simulated group replied `OK` to an L1 at 18:58. Four hours later, at 23:00, a fresh L1; then L2 at 01:00 and a rescue case at 05:00. The group was asleep in a lodge.

**Cause:** OK extended the deadline by a flat 4 hours, which landed in the night.

**Fix:** an OK whose 4 hours would end between 20:00 and 06:00 lasts until 09:00. **Test:** `test_ok_in_the_evening_waits_for_morning`. Found by the simulation.

### 12. My night-time fix for false rescue cases did nothing

**Symptom:** after #11, most false rescue cases still opened at night. Since helicopters only fly in daylight, I added an option to hold any rescue case due between 20:00 and 06:00 until 06:30.

**Result:** false rescue cases went from 288 to 288. Median time to a rescue case for real accidents went from 13.0 to 21.2 hours.

**Cause:** I'd assumed those groups would be found during the night. They aren't: their messages arrive when they walk into signal the next morning, after 06:30. Holding the case changed only who waited, and it was the people in real trouble.

**Fix:** rejected. The option stays in the code switched off (`DEFER_NIGHT_RESCUE_CASE = False`) and in the results table as evidence. **Lesson:** run the experiment before believing the mechanism.

### 13. A sweeper outage longer than a day loses groups

**Symptom:** a handler test set the clock 28 hours after a group's deadline and the sweeper found nothing.

**Cause:** the sweeper reads the last 24 hourly buckets. A deadline older than that is never in any bucket it reads.

**Fix:** the alarm fires after 15 minutes, so 24 hours is plenty in normal operation; but after a long outage the runbook now invokes the sweeper once with `{"lookback_h": 72}`. **Test:** `test_sweeper_catches_up_after_a_long_outage_when_asked`. Found by a test I wrote for something else.

### 14. The first crossing index scanned the whole table

**Symptom:** caught in review of my own `store.py`: to replace a trek's `PASS#` items after a plan change, the first version scanned the table for items with that trek id.

**Cause:** I didn't keep the old crossings anywhere, so I searched for them. A scan reads every item in the table, every time a guide sends `STAY`.

**Fix:** the engine computes the crossings before and after every change (it has both versions of the trek in memory) and the store writes only the difference. No reads at all.

## Infrastructure

### 15. The weather function inherited a runtime it can't have

**Symptom:** `cfn-lint` error E3685: container image functions cannot specify Handler, Runtime or Layers. A second run failed to parse at all: `did not find expected ',' or '}'`.

**Cause:** `Runtime` and `CodeUri` were in `Globals`, which applied them to the one container-image function too. The parse error was `Path: /treks/{trek_id}` inside a YAML flow mapping, where `{` starts a nested map.

**Fix:** runtime and code location on each zip function; paths with braces quoted. Found by the linter, which runs in CI.

### 16. A JWT authorizer can't check a Cognito group

**Symptom:** I wanted only the `pass-wardens` group to reach `POST /passes/{id}/clear` and looked for the setting on the HTTP API route.

**Cause:** HTTP API JWT authorizers check scopes, not Cognito groups. Without a check, any agency account could lift a storm hold.

**Fix:** the handler reads `cognito:groups` from the claims and returns 403 unless `pass-wardens` is there. **Test:** `test_weather_poll_issues_a_hold_and_only_a_warden_lifts_it` (an `agencies` user gets 403).
