"""Hydromatik quotation estimator (Streamlit UI).

Run:  streamlit run app.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
import streamlit as st

from hydroquote import (CostInputs, Feature, FeatureType, PartSpec, QuoteLine, ReferencePart,
                        calibrate, compute_cost, estimate_with_calibration, load_calibration,
                        load_machine, load_reference_parts)
from hydroquote.calibration import (MIN_POINTS_FOR_TWO_PARAMS, REFERENCE_DIR, save_calibration,
                                    save_reference_part)
from hydroquote.excel_io import build_straight_workbook
from hydroquote.machine import MachineProfile, save_machine

SAMPLES_DIR = Path(__file__).parent / "samples" / "drawings"
FEATURE_COLUMNS = ["end", "type", "dia", "length", "pitch", "qty", "note"]
FEATURE_TYPES = [t.value for t in FeatureType]

st.set_page_config(page_title="Hydromatik quote estimator", layout="wide")

ss = st.session_state
ss.setdefault("draft", None)          # PartSpec being edited
ss.setdefault("draft_source", "")
ss.setdefault("quote", [])            # list[QuoteLine]
ss.setdefault("editor_key", 0)


def _set_draft(part: PartSpec, source: str) -> None:
    ss.draft = part
    ss.draft_source = source
    ss.editor_key += 1


def _features_df(part: PartSpec) -> pd.DataFrame:
    rows = [{**f.model_dump(), "type": f.type.value} for f in part.features]
    return pd.DataFrame(rows, columns=FEATURE_COLUMNS)


def _features_from_df(df: pd.DataFrame) -> tuple[list[Feature], list[str]]:
    feats, errors = [], []
    for i, row in df.iterrows():
        if pd.isna(row.get("type")) or pd.isna(row.get("dia")):
            continue
        try:
            feats.append(Feature(
                end=row["end"] if row["end"] in ("A", "B") else "A",
                type=row["type"], dia=float(row["dia"]),
                length=0.0 if pd.isna(row["length"]) else float(row["length"]),
                pitch=0.0 if pd.isna(row["pitch"]) else float(row["pitch"]),
                qty=1 if pd.isna(row["qty"]) else int(row["qty"]),
                note="" if pd.isna(row["note"]) else str(row["note"]),
            ))
        except Exception as e:  # noqa: BLE001 - show the row error to the user
            errors.append(f"Row {i + 1}: {e}")
    return feats, errors


machine = load_machine()
cal = load_calibration()

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("Hydromatik estimator")
    st.caption(machine.name)
    st.metric("Machining rate", f"Rs {machine.machining_rate_rs_per_min:g} / min")
    st.markdown(
        f"**Calibration** ({cal.mode}, {cal.n_parts} parts)  \n"
        f"cycle time = {cal.cut_factor:g} x machine time + {cal.overhead_min:g} min  \n"
        f"leave-one-out error: {cal.loo_mae_min} min"
    )
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    st.markdown(f"**Drawing reader (Claude):** {'ready' if has_key else 'no API key set'}")
    include_nf = st.checkbox(
        "Include NUT|RED N and FERULE/WN in Net", value=False,
        help="The original Straight sheet leaves columns S and T out of Net. Tick to include them.")

tab_quote, tab_cal, tab_machine = st.tabs(["Quote", "Calibration", "Machine & cutting data"])

# ---------------------------------------------------------------- quote tab
with tab_quote:
    st.subheader("1. Part from drawing")
    src = st.radio("Source", ["Upload drawing (Claude reads it)", "Reference / sample part", "Blank part"],
                   horizontal=True)

    if src.startswith("Upload"):
        up = st.file_uploader("Drawing (PDF, PNG or JPG)", type=["pdf", "png", "jpg", "jpeg"])
        if up is not None and st.button("Read drawing", type="primary", disabled=not has_key):
            from hydroquote.extraction import ExtractionError, extract_part_from_drawing
            with st.spinner("Claude is reading the drawing..."):
                try:
                    _set_draft(extract_part_from_drawing(up.getvalue(), up.name), f"drawing {up.name}")
                except ExtractionError as e:
                    st.error(str(e))
        if not has_key:
            st.info("Set ANTHROPIC_API_KEY before starting the app to read drawings automatically. "
                    "Without it, use a reference part or enter the features by hand.")
    elif src.startswith("Reference"):
        refs = load_reference_parts()
        names = [r.part.description for r in refs]
        pick = st.selectbox("Part", names)
        if st.button("Load part"):
            ref = refs[names.index(pick)]
            _set_draft(ref.part.model_copy(deep=True), f"reference ({ref.actual_cycle_time_min} min actual)")
    else:
        if st.button("Start blank part"):
            _set_draft(PartSpec(description="", bar_size=19, overall_length=30), "manual entry")

    part: PartSpec | None = ss.draft
    if part is None:
        st.info("Load a drawing, a reference part or a blank part to start.")
    else:
        st.caption(f"Loaded from: {ss.draft_source}. Check every value against the drawing before quoting.")
        for w in part.warnings:
            st.warning(w)

        c = st.columns([3, 2, 1, 1, 1, 1])
        k = ss.editor_key
        description = c[0].text_input("Description", part.description, key=f"desc{k}")
        drawing_no = c[1].text_input("Drawing no.", part.drawing_no, key=f"dwg{k}")
        material = c[2].text_input("RM", part.material, key=f"rm{k}")
        shape = c[3].selectbox("Shape", ["S", "R"], index=0 if part.shape == "S" else 1, key=f"shape{k}",
                               help="S = hex bar, R = round bar")
        bar_size = c[4].number_input("Y (hex AF mm)", value=float(part.bar_size), min_value=1.0, key=f"y{k}")
        length = c[5].number_input("L (mm)", value=float(part.overall_length), min_value=1.0, key=f"l{k}")

        st.markdown("**Machining features** (end A = first setup, end B = after flipping)")
        edited = st.data_editor(
            _features_df(part), key=f"feat{k}", num_rows="dynamic", use_container_width=True,
            column_config={
                "end": st.column_config.SelectboxColumn("End", options=["A", "B"], required=True),
                "type": st.column_config.SelectboxColumn("Feature", options=FEATURE_TYPES, required=True),
                "dia": st.column_config.NumberColumn("Dia mm", format="%.3f"),
                "length": st.column_config.NumberColumn("Length / depth / width mm", format="%.2f"),
                "pitch": st.column_config.NumberColumn("Pitch mm", format="%.3f"),
                "qty": st.column_config.NumberColumn("Qty", min_value=1, step=1),
                "note": st.column_config.TextColumn("Note"),
            },
        )
        feats, errors = _features_from_df(edited)
        for e in errors:
            st.error(e)
        part = PartSpec(description=description, drawing_no=drawing_no, material=material, shape=shape,
                        bar_size=bar_size, overall_length=length, features=feats, warnings=part.warnings)

        st.subheader("2. Cycle time (TL)")
        est = estimate_with_calibration(part, machine, cal)
        m = st.columns(4)
        m[0].metric("Machine time (physics)", f"{est.physics_min:.2f} min")
        m[1].metric("Estimated cycle time", f"{est.cycle_time_min:.2f} min")
        m[2].metric(f"Rounded ({cal.rounding_step_min:g} min step)", f"{est.cycle_time_rounded_min:g} min")
        override = m[3].number_input("Cycle time to quote (min)", value=float(est.cycle_time_rounded_min),
                                     min_value=0.0, step=0.25, key=f"ct{k}-{est.cycle_time_rounded_min}")
        for w in est.warnings:
            st.warning(w)
        with st.expander("Operation-by-operation breakdown"):
            st.dataframe(pd.DataFrame(est.breakdown_rows()), use_container_width=True, hide_index=True)
            st.caption(f"Cutting {est.cutting_s:.0f} s + non-cutting {est.noncut_s:.0f} s = "
                       f"{est.physics_min:.2f} min; x {cal.cut_factor:g} + {cal.overhead_min:g} min "
                       f"calibration = {est.cycle_time_min:.2f} min")

        st.subheader("3. Cost (Straight sheet)")
        ci = st.columns(6)
        qty = ci[0].number_input("Quantity", value=5.0, min_value=0.0, key=f"qty{k}")
        rm_rate = ci[1].number_input("RM rate Rs/kg", value=85.0, min_value=0.0, key=f"rmr{k}")
        margin = ci[2].number_input("Mgn", value=1.3, min_value=0.0, step=0.05, key=f"mgn{k}")
        caps = ci[3].number_input("Caps", value=0.0, min_value=0.0, key=f"caps{k}")
        o_ring = ci[4].number_input("O Ring", value=0.0, min_value=0.0, key=f"oring{k}")
        crimp = ci[5].number_input("Crimp N", value=0.0, min_value=0.0, key=f"crimp{k}")
        ci2 = st.columns(6)
        nut = ci2[0].number_input("NUT|RED N", value=0.0, min_value=0.0, key=f"nut{k}")
        ferrule = ci2[1].number_input("FERULE/WN", value=0.0, min_value=0.0, key=f"fer{k}")
        cn = ci2[2].number_input("CN", value=0.0, min_value=0.0, key=f"cn{k}")
        was = ci2[3].number_input("Was", value=0.0, min_value=0.0, key=f"was{k}")
        sr = ci2[4].number_input("SR Ring", value=0.0, min_value=0.0, key=f"sr{k}")
        cost_in = CostInputs(quantity=qty, raw_material_rate=rm_rate, caps=caps, crimp_n=crimp,
                             nut_red_n=nut, ferrule_wn=ferrule, cn=cn, was=was, o_ring=o_ring,
                             sr_ring=sr, margin=margin)
        cost = compute_cost(part, cost_in, override, machine.machining_rate_rs_per_min, include_nf)
        cm = st.columns(7)
        cm[0].metric("RM weight", f"{cost.rm_weight_kg * 1000:.0f} g")
        cm[1].metric("RM cost", f"Rs {cost.rm_cost:.2f}")
        cm[2].metric("Cutting", f"Rs {cost.cutting:.2f}")
        cm[3].metric("Machining", f"Rs {cost.machining:.2f}")
        cm[4].metric("Plating", f"Rs {cost.plating:.2f}")
        cm[5].metric("Net", f"Rs {cost.net:.2f}")
        cm[6].metric("S.P. / pc", f"Rs {cost.selling_price:.2f}")

        b = st.columns([1, 1, 2])
        if b[0].button("Add to quote", type="primary", disabled=not part.description):
            manual = abs(override - est.cycle_time_rounded_min) > 1e-9
            ss.quote.append((QuoteLine(part=part, cost=cost_in, cycle_time_min=override,
                                       cycle_time_source="manual" if manual else "estimated"),
                             None if manual else est))
            st.success(f"Added {part.description}")
        with b[2].popover("Save as reference part (known cycle time)"):
            actual = st.number_input("Actual / agreed cycle time (min)", min_value=0.1, value=float(override),
                                     key=f"actual{k}")
            note = st.text_input("Source", "shop-floor time study", key=f"src{k}")
            if st.button("Save reference", disabled=not part.description):
                path = save_reference_part(ReferencePart(part=part, actual_cycle_time_min=actual, source=note))
                st.success(f"Saved {path.name}. Refit on the Calibration tab.")

    st.subheader("4. Quotation")
    if not ss.quote:
        st.caption("No lines yet.")
    else:
        rows = []
        for i, (line, _) in enumerate(ss.quote, start=1):
            cb = compute_cost(line.part, line.cost, line.cycle_time_min,
                              machine.machining_rate_rs_per_min, include_nf)
            rows.append({"#": i, "Description": line.part.description, "Qty": line.cost.quantity,
                         "Y": line.part.bar_size, "L": line.part.overall_length,
                         "Cycle time": line.cycle_time_min, "TL source": line.cycle_time_source,
                         "Net": round(cb.net, 2), "S.P.": round(cb.selling_price, 2),
                         "Value": round(cb.total_value, 2)})
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.metric("Quotation total", f"Rs {df['Value'].sum():,.2f}")
        q = st.columns([1, 1, 3])
        xlsx = build_straight_workbook([l for l, _ in ss.quote], [e for _, e in ss.quote],
                                       machine.machining_rate_rs_per_min)
        q[0].download_button("Download Excel", xlsx, "quotation_straight.xlsx",
                             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        if q[1].button("Clear quote"):
            ss.quote = []
            st.rerun()
        if include_nf:
            st.caption("Note: the Excel keeps the original Net formula (S and T excluded).")

# ---------------------------------------------------------------- calibration tab
with tab_cal:
    st.subheader("Reference parts with known cycle times")
    st.write(
        "The physics model gives pure machine time. The shop's real cycle time also includes "
        "handling, deburring, inspection and the speeds actually used, so it is mapped with "
        "`cycle time = factor x machine time + overhead`, fitted on these parts. "
        f"Below {MIN_POINTS_FOR_TWO_PARAMS} parts only the factor is fitted. "
        "Add parts with real shop-floor times to make it better."
    )
    refs = load_reference_parts()
    ref_df = pd.DataFrame([{
        "file": p.name, "use": r.use_for_calibration, "description": r.part.description,
        "actual (min)": r.actual_cycle_time_min, "source": r.source,
    } for p, r in zip(sorted(REFERENCE_DIR.glob("*.json")), refs)])
    edited_refs = st.data_editor(ref_df, hide_index=True, use_container_width=True,
                                 disabled=["file", "description", "source"])
    step = st.number_input("Round cycle time to (min)", value=float(cal.rounding_step_min),
                           min_value=0.0, step=0.05)
    if st.button("Refit calibration", type="primary"):
        for (_, row), r in zip(edited_refs.iterrows(), refs):
            r.use_for_calibration = bool(row["use"])
            r.actual_cycle_time_min = float(row["actual (min)"])
            (REFERENCE_DIR / row["file"]).write_text(r.model_dump_json(indent=2) + "\n")
        cal = calibrate(load_reference_parts(), machine, rounding_step_min=step)
        save_calibration(cal)
        st.success("Calibration saved.")
    st.markdown(
        f"**Current fit:** {cal.mode}, factor {cal.cut_factor:g}, overhead {cal.overhead_min:g} min, "
        f"{cal.n_parts} parts, fitted {cal.fitted_on}  \n"
        f"Mean error {cal.mae_min} min; leave-one-out error {cal.loo_mae_min} min; "
        f"leave-one-out error of just quoting the average: {cal.baseline_loo_mae_min} min."
    )
    if cal.loo_mae_min is not None and cal.baseline_loo_mae_min is not None \
            and cal.loo_mae_min > cal.baseline_loo_mae_min:
        st.warning("On the current reference parts the model does not yet beat simply quoting the "
                   "average cycle time. The reference times do not follow part geometry closely "
                   "(e.g. near-identical parts quoted 4.0 and 4.5 min). Measured shop-floor times "
                   "for more parts are needed before relying on small differences.")
    if cal.residuals:
        st.dataframe(pd.DataFrame([r.model_dump() for r in cal.residuals]),
                     use_container_width=True, hide_index=True)

# ---------------------------------------------------------------- machine tab
with tab_machine:
    st.subheader(machine.name)
    st.caption(machine.source)
    st.write("Shop timings and cutting data used by the physics model. Change them to match "
             "the floor, then refit the calibration.")
    text = st.text_area("Machine profile (JSON)", json.dumps(machine.model_dump(), indent=2), height=520)
    if st.button("Save machine profile"):
        try:
            save_machine(MachineProfile.model_validate(json.loads(text)))
            st.success("Saved. Refit the calibration on the Calibration tab.")
        except Exception as e:  # noqa: BLE001
            st.error(f"Invalid profile: {e}")
