# Storm holds: the rule and the hindcast

## The rule

Four times a day, for each pass:

1. Read the GEFS control forecast's 6-hourly precipitation for the next 72 hours at the pass's 0.25° grid cell.
2. Find the wettest 24-hour window that starts in the next 48 hours.
3. **HOLD** if it is 30 mm or more; **WATCH** if 15 mm or more; otherwise CLEAR.

At 4,600-5,500 m in March-May and October-November, nearly all of that falls as snow; 30 mm of water is very roughly 30 cm of new snow, more when it's cold and dry. I don't convert, because the threshold is calibrated against the forecast's own numbers (below), not against centimetres on the trail.

Why the wettest 24 hours in 48 rather than "tomorrow": groups decide the evening before and leave Thorong Phedi at 04:00, and the 2014 storm built over two days. Why the control member rather than the ensemble: the reforecast has 5 members on most days, the live run 31; I score the control so the hindcast and production use exactly the same input. The ensemble agreement is still informative (all five members over the line on 13 October 2014) and is the first thing I'd add to the message.

## The hindcast

- **Forecasts:** GEFSv12 reforecast, control member, every 00 UTC run, March-May and October-November, 2010-2019. 1,530 runs.
- **Yardstick:** ERA5 daily snowfall at the same grid cells.
- **A dangerous day:** 20 mm (water) or more of snowfall in a UTC day.
- **A hit:** the forecast run on day *d* said HOLD, and day *d* or *d+1* was dangerous.
- **Counting:** Cho La, Renjo La and Kongma La share a grid cell, so I count 4 cells, not 6 passes: 6,040 forecast days, 30 dangerous ones.

### Threshold sweep

| HOLD at (mm) | Dangerous days warned | Missed | False holds | Hold days per pass per season |
|---:|---:|---:|---:|---:|
| 10 | 29 (97%) | 1 | 843 | 10.9 |
| 15 | 27 (90%) | 3 | 432 | 5.7 |
| 20 | 24 (80%) | 6 | 243 | 3.3 |
| 25 | 24 (80%) | 6 | 139 | 2.0 |
| **30** | **24 (80%)** | **6** | **68** | **1.1** |
| 35 | 24 (80%) | 6 | 41 | 0.8 |
| 40 | 22 (73%) | 8 | 21 | 0.5 |
| 50 | 17 (57%) | 13 | 9 | 0.3 |
| 60 | 13 (43%) | 17 | 3 | 0.2 |

From 20 to 35 mm the rule catches the same 24 days while holds fall fourfold. I took 30 rather than 35: with only 30 dangerous days in ten years, the plateau's edges are uncertain, and the cost of the extra 0.3 holds a season is a day's wait. WATCH at 15 mm catches 27 of 30.

### Per pass at HOLD = 30 mm

| Cell | Dangerous days | Hit | Missed | False holds | Holds per season |
|---|---:|---:|---:|---:|---:|
| Thorong La | 10 | 7 | 3 | 9 | 0.8 |
| Larkya La | 8 | 7 | 1 | 37 | 2.2 |
| Cho La / Renjo La / Kongma La | 12 | 10 | 2 | 8 | 0.9 |
| Laurebina La | 0 | 0 | 0 | 14 | 0.7 |

**Larkya La** sits in a wetter cell on the Manaslu side; one threshold for all passes gives it twice the holds. **Laurebina La** never has a dangerous day in ERA5 at all ([bug #5](CHALLENGES_AND_BUGS.md#5-laurebina-la-never-snows)), so every hold there scores as false, though some of those were wet, cold days on a 4,610 m pass. Both argue for **per-pass thresholds**, which I haven't fitted: with 8 dangerous days at Larkya La, a fitted threshold would be fitted to noise. A season of shadow-mode data with wardens' notes is the honest way to set them.

Of the 68 false holds, 33 came on days with at least 5 mm and 12 on days with at least 10 mm of snow: "false" by my 20 mm line, but not dry. None came in November, which matches the climate: the post-monsoon storms that matter come in October, and November is the clear, cold month.

### The misses

| Forecast run | Cell | Snow that fell (max day) | Forecast | Level |
|---|---|---:|---:|---|
| 12 Oct 2014 | Larkya La | 28.4 | 14.0 | CLEAR |
| 12 Oct 2014 | Thorong La | 26.5 | 18.5 | WATCH |
| 27 May 2016 | Thorong La | 22.9 | 3.4 | CLEAR |
| 28 May 2016 | Thorong La | 22.9 | 12.7 | CLEAR |
| 30 Oct 2010 | Khumbu | 21.0 | 19.5 | WATCH |
| 31 Oct 2010 | Khumbu | 21.0 | 15.1 | WATCH |

The two from 12 October 2014 are the first day of Hudhud, two days ahead; the next morning's run put both passes on HOLD. The late-May 2016 storm at Thorong La is a real miss: the forecast didn't see it the day before or the day of. Late May is the edge of the pre-monsoon season, when convective snow is hard to forecast at 25 km.

### Hudhud, October 2014

| Run (00 UTC) | Control | 5 members at HOLD | Level | ERA5 snow that UTC day |
|---|---:|---:|---|---:|
| 10 Oct | 0.5 | 0 | CLEAR | 0.5 |
| 11 Oct | 0.3 | 0 | CLEAR | 0.1 |
| 12 Oct | 18.5 | 0 | WATCH | 0.0 |
| **13 Oct** | **103.9** | **5** | **HOLD** | 26.5 |
| 14 Oct | 86.9 | 5 | HOLD | **94.7** |
| 15 Oct | 0.0 | 0 | CLEAR (the hold stays until a warden lifts it) | 0.1 |

The 00 UTC run is complete around 05:30 UTC, 11:15 Nepal time; the poller runs at 05:30 UTC. A HOLD message on the 13th, before lunch, reaches groups at Manang, Yak Kharka and Thorong Phedi the day before they would have crossed.

## What this can't tell you

- **The reforecast is today's model on yesterday's weather.** It shows how well a system running now would warn. It doesn't show that 2014's forecasts were this good.
- **ERA5 isn't the trail.** A 25 km cell averages the pass with the valleys; it underestimates the heaviest local snow and the cell's mean elevation is far below the pass. The rule is calibrated to agree with ERA5 about *which days* were bad, which is the question that matters for a hold.
- **Snow isn't the only hazard.** Wind and cold on the pass, and avalanche danger for days after a storm, aren't in the rule. The human lift (ADR-006) is how the days after are handled.
- **Thirty dangerous days is a small sample.** The percentages above have wide error bars. I report counts next to every rate for that reason.

## Reproduce

```bash
make data       # re-pull the extracts from the public buckets (~40 min, nothing stored but the points)
make hindcast   # this document's tables, artifacts/reports/hindcast.json and the two charts
```
