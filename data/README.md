# Data

Everything here is either open data pulled from public AWS buckets by the scripts in `scripts/`, or a small hand-built table I describe below. Nothing needs an account or an API key.

## Weather (real, open)

| File | What | Source | How it was made |
|---|---|---|---|
| `processed/gefs_pass_precip_6h.csv` | 6-hourly forecast precipitation (mm) for the first 72 h of every 00 UTC control forecast, March-May and October-November 2010-2019, at the grid cell of each pass. 1,530 forecasts, 110,202 rows. | NOAA **GEFSv12 reforecast**, `s3://noaa-gefs-retrospective` (NOAA Open Data Dissemination, AWS Open Data) | `scripts/fetch_gefs_reforecast.py` reads the `.idx` file and fetches only the APCP 6-hour messages by byte range (~3 MB per forecast instead of 25 MB), decodes them with ecCodes |
| `processed/gefs_hudhud_2014.csv` | Same, for all five ensemble members, every run from 8 to 15 October 2014 | same | `--dates 2014-10-08:2014-10-15 --members c00,p01,p02,p03,p04` |
| `processed/era5_pass_snowfall_daily.csv` | Daily snowfall (mm water equivalent) per pass per UTC day, same seasons | **ERA5** reanalysis (ECMWF / Copernicus), NSF NCAR copy at `s3://nsf-ncar-era5` (dataset ds633.0), variable 144 `SF` | `scripts/fetch_era5_snowfall.py` streams each half-month global file (~150 MB; 100 files, ~16 GB in total), keeps the six points, sums hourly values per UTC day |
| `passes.json` | The six passes: name, route, approximate coordinates, elevation | Commonly published figures | By hand. Coordinates are good to about 2 km, far finer than the 25 km grids they're matched to |

The live system reads the operational forecast from `s3://noaa-gefs-pds` (the 0.25° `pgrb2sp25` control files) with the same parser.

**Things to know about these numbers**

- **The reforecast is a modern model run on old dates.** GEFSv12 was produced around 2019-2020. Scoring it on 2014 tells you how well *today's* model would have warned, which is what matters for a system running today. It does not say 2014's operational forecasts were this good.
- **ERA5 is the yardstick, not the truth on the trail.** A 0.25° reanalysis cell around Thorong La averages ridges and valleys; it smooths out the worst local snowfall and puts the cell's mean elevation far below the pass. Laurebina La's cell (4,610 m pass) never reaches 10 mm of snow in a day in ERA5; I discuss this in [CHALLENGES_AND_BUGS.md](../docs/CHALLENGES_AND_BUGS.md#5-laurebina-la-never-snows).
- **Cho La, Renjo La and Kongma La share one grid cell** in both datasets. The hindcast counts that cell once.
- `raw/` (gitignored) holds the per-file ERA5 cache, so a re-run only fetches what's missing.

## The trail network (hand-built)

`trails.json` holds 38 overnight stops on the Annapurna Circuit, Manaslu Circuit, Everest Base Camp and Three Passes, and Gosainkunda-Helambu, the 37 trail sections between them with typical walking hours, which section crosses which pass, and five standard itineraries.

- **Elevations** are the commonly published figures.
- **Walking hours** are typical times from standard published itineraries, rounded to the half hour. Real groups vary; the system learns each group's pace and adds grace.
- **Connectivity** (`good`, `patchy`, `none`) is **my assumption** of whether a group can usually send a check-in from that stop the evening it arrives. It drives the most important design decision (no deadline where there's no signal), so the first thing a real deployment must do is confirm it with agencies and lodge owners.
- **Aliases** are the spellings I've seen for the same place ("Thorung", "Thorang", "Thorong"; "Gorakshep"; "Tyangboche").

## Simulated (clearly labelled)

The season simulation (`bato/simulate.py`) makes up 1,500 trekking groups. Every behavioural number is a named constant at the top of that file and discussed in [GRACE_POLICY.md](../docs/GRACE_POLICY.md). None of it is presented as observed data.
