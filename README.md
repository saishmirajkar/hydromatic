# Hydromatik quotation estimator (Straight sheet)

Turns a fitting drawing into a filled row of the **Straight** cost sheet. That includes the
value that used to take the most time: **Cycle Time** (column N, minutes per piece), which
drives `Machining Rate = Cycle Time x 3.75`.

> Note on naming: in the original workbook the column titled **Tl** (column I) is
> `(L + 6) / 10`, the billet length in cm used for raw-material weight. The hand-estimated
> time is column **N, Cycle Time**. This tool estimates column N and fills in all other columns.

## How it works

```
drawing PDF --> Claude reads it --> part + machining features --> you review / edit
                                                                        |
     Straight-sheet costing <-- cycle time <-- calibration <-- physics time per operation
             |
             +--> Excel (same columns and formulas as the original sheet)
```

1. **Drawing reading** (`hydroquote/extraction.py`): the PDF is sent to Claude together
   with the vector dimension text in the PDF. The output is constrained to a schema:
   description, drawing no., material, hex AF (Y), length (L) and a list of features per
   setup (turned diameters, threads, grooves, drills, bores, 24° cones, chamfers).
   Needs `ANTHROPIC_API_KEY`. Without a key, features can be typed in or loaded from a
   reference part.
2. **Cycle-time engine** (`hydroquote/cycle_time.py`): each feature becomes a lathe
   operation on setup A or B. Cutting time comes from length, feed and spindle speed
   (cutting speed for the material, capped at the Smarturn's 4500 rpm). Non-cutting time
   is added for turret indexing, approach and load/unload. The full breakdown is shown and
   exported, so an estimator can see *why* a part takes the time it does.
3. **Calibration** (`hydroquote/calibration.py`): pure machine time is converted to the
   shop's quoted cycle time with `cycle time = factor x machine time + overhead`, fitted on
   reference parts with known times (`data/reference_parts/`). Below 8 parts only the
   factor is fitted.
4. **Costing** (`hydroquote/costing.py`): the Straight sheet formulas, reproduced exactly
   (tested against all 8 rows of the original workbook). Columns S (NUT|RED N) and
   T (FERULE/WN) are left out of Net, as in the original sheet. A checkbox in the app
   includes them.
5. **Excel export** (`hydroquote/excel_io.py`): a `Straight` sheet with the original
   columns and live formulas (estimated cycle times are highlighted), plus a
   `TL breakdown` sheet with every operation.

## Run it

The client's drawings, cost sheet and machine quotation are **not committed** because this
repository is public. Put them in `samples/` locally (`samples/cost_sheet.xlsx`,
`samples/drawings/*.pdf`) to run the tests that compare against the original workbook;
without them those tests are skipped.


```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...        # optional, enables automatic drawing reading
streamlit run app.py
```

Command line:

```bash
python cli.py extract samples/drawings/G_10_S.pdf > part.json   # needs API key
python cli.py estimate part.json                                 # cycle time + cost
python cli.py calibrate                                          # refit from reference parts
python -m pytest -q
```

## Current calibration and its limits

The 5 parts with drawings come out at about 1.9–2.2 min of pure machine time. They were
quoted at 4.0–5.0 min, so the fitted factor is **≈ 2.13**. The rest is handling,
deburring, inspection and real-world speeds.

| Part | Quoted (min) | Estimated (min) |
|---|---|---|
| EGE 12 X 1/4 - L - ED | 5.0 | 4.06 |
| GE 10 X 1/4 - L - A | 4.5 | 4.18 |
| GE 12 X 3/8 - L - B | 4.5 | 4.45 |
| GE 12 X M16 X 1.5 - L - ED | 4.0 | 4.71 |
| G 10 - S | 4.0 | 4.46 |

Mean error is 0.5 min. **On these 5 parts the model does not yet beat simply quoting the
average (4.4 min).** The historical times don't follow the geometry: GE 12×M16-ED has
more features than GE 12×3/8-B but was quoted 0.5 min lower. The model adds value on parts
that differ from these (longer, bigger hex, extra threads/grooves), where a flat average is
wrong. It needs more reference parts with **measured** shop-floor times to be accurate:

- Use *Save as reference part* in the app (or add a JSON file to `data/reference_parts/`),
  then *Refit calibration*.
- Tune the shop timings and cutting data in `data/machine_profile.json` (load/unload time,
  feeds, speeds) to match the floor.

## Assumptions to confirm

- Parts are machined from sawn hex billets on the LMW Smarturn in two setups (A then B).
  Some drawings say "SHOP COPY (Automat)". If those parts run on cam automats, they need
  their own machine profile and calibration.
- The reference features in `data/reference_parts/` were transcribed by hand from the
  drawings. The EGE male-cone end is approximated.
- G 10-L, GR 12×10-L and RED 12×10-L have no drawings in the samples, so they are not
  used for calibration.

## Layout

```
app.py                      Streamlit UI (quote, calibration, machine data)
cli.py                      command-line entry points
hydroquote/                 models, extraction, cycle_time, calibration, costing, excel_io
data/machine_profile.json   Smarturn limits, shop timings, cutting data
data/calibration.json       current fit
data/reference_parts/       parts with known cycle times (calibration set)
samples/                    original cost sheet, drawings, machine quotation (local only, not in git)
tests/                      pytest suite
```
