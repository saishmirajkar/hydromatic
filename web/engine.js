/*
 * Hydromatik estimator engine (browser + Node).
 *
 * JavaScript port of hydroquote/cycle_time.py, calibration.py, costing.py and
 * the Excel layout of excel_io.py. Keep the two in step: tests/test_web_engine.py
 * runs both on the reference parts and compares the results.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.HQ = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const FEATURE_TYPES = [
    "turn", "taper_ext", "thread_ext", "thread_int", "groove",
    "drill", "bore", "cone_24", "chamfer", "hex_chamfer",
  ];

  const DEFAULT_MACHINE = {
    name: "LMW Smarturn CNC lathe (Fanuc 0i-TF)",
    source: "LMW quotation Ref-LMW/SP/266 dated 04/02/2021 (8 machines)",
    max_rpm: 4500,
    spindle_power_kw: 5.5,
    turret_stations: 8,
    chuck_mm: 165,
    machining_rate_rs_per_min: 3.75,
    setups_per_part: 2,
    load_unload_s_per_setup: 15.0,
    spindle_start_stop_s_per_setup: 3.0,
    tool_index_s: 1.0,
    approach_retract_s_per_tool: 2.0,
    facing_stock_per_side_mm: 3.0,
    cutting_data: {
      CS: {
        turn: { vc: 180, feed: 0.20, doc: 1.5, finish_feed: 0.10 },
        face: { vc: 160, feed: 0.15, doc: 1.5 },
        groove: { vc: 90, feed: 0.05 },
        thread: { vc: 100, infeed_per_pass: 0.12, spring_passes: 1, return_s_per_pass: 0.6 },
        drill: { vc: 70, feed_per_mm_dia: 0.015, peck_ratio: 3.0, peck_penalty: 1.4 },
        bore: { vc: 140, feed: 0.10, doc: 1.0 },
        chamfer: { seconds_each: 1.5 },
        hex_chamfer: { seconds_each: 2.5 },
      },
    },
  };

  // Used only when there are no reference parts to fit on (value of the
  // fit on the 5 sample drawings).
  const DEFAULT_CALIBRATION = {
    cut_factor: 2.1337, overhead_min: 0, rounding_step_min: 0.25, mode: "built-in default",
    n_parts: 0, mae_min: null, loo_mae_min: null, baseline_loo_mae_min: null, residuals: [],
  };

  const MIN_POINTS_FOR_TWO_PARAMS = 8;
  const HEX_CORNER_FACTOR = 1 / Math.cos(Math.PI / 6);
  const GROOVE_TOOL_WIDTH_MM = 2.0;
  const CONE_HALF_ANGLE_DEG = 12.0;

  const num = (v, d = 0) => {
    const n = typeof v === "string" ? parseFloat(v) : v;
    return Number.isFinite(n) ? n : d;
  };

  /** Coerce anything shaped like a part (Claude output, stored row) into a clean part. */
  function normalizePart(raw) {
    const p = raw || {};
    const warnings = Array.isArray(p.warnings) ? p.warnings.map(String) : [];
    const features = [];
    (Array.isArray(p.features) ? p.features : []).forEach((f, i) => {
      const type = String((f && f.type) || "").trim();
      const dia = num(f && f.dia, NaN);
      if (!FEATURE_TYPES.includes(type) || !(dia > 0)) {
        warnings.push(`Feature ${i + 1} skipped (type "${type}", dia ${f && f.dia}).`);
        return;
      }
      features.push({
        end: f.end === "B" ? "B" : "A",
        type,
        dia,
        length: Math.max(0, num(f.length)),
        pitch: Math.max(0, num(f.pitch)),
        qty: Math.max(1, Math.round(num(f.qty, 1))),
        note: f.note ? String(f.note) : "",
      });
    });
    return {
      description: String(p.description || "").trim(),
      drawing_no: String(p.drawing_no || "").trim(),
      material: String(p.material || "CS").trim() || "CS",
      shape: p.shape === "R" ? "R" : "S",
      bar_size: num(p.bar_size, 19),
      overall_length: num(p.overall_length, 30),
      features,
      warnings,
    };
  }

  const rpm = (dia, vc, maxRpm) => Math.min(1000 * vc / (Math.PI * Math.max(dia, 1)), maxRpm);
  const cutSeconds = (length, feed, r) => 60 * length / (feed * r);

  function barEnvelopeDia(part) {
    return part.shape === "S" ? part.bar_size * HEX_CORNER_FACTOR : part.bar_size;
  }

  function parentOd(feature, endTurns, envelope) {
    const c = endTurns
      .filter((t) => t !== feature && t.dia > feature.dia && t.length >= feature.length)
      .map((t) => t.dia);
    return c.length ? Math.min(...c) : envelope;
  }

  function preHole(feature, part) {
    const c = part.features
      .filter((f) => f !== feature && f.dia < feature.dia &&
        (f.type === "drill" || (f.end === feature.end && (f.type === "bore" || f.type === "cone_24"))))
      .map((f) => f.dia);
    return c.length ? Math.max(...c) : 0;
  }

  function toolFor(f) {
    switch (f.type) {
      case "turn": case "taper_ext": case "chamfer": case "hex_chamfer": return "OD turning";
      case "groove": return "grooving";
      case "thread_ext": return "OD threading";
      case "thread_int": return "ID threading";
      case "bore": case "cone_24": return "boring bar";
      case "drill": return `drill ${fmt(f.dia)}`;
    }
    return "OD turning";
  }

  // Python's "%g"-like formatting for dimensions in labels.
  function fmt(x) {
    return String(Number(x.toPrecision(6)));
  }

  /**
   * Bottom-up cycle time. Returns machine time from first principles and the
   * calibrated cycle time: cut_factor * physics + overhead.
   */
  function estimateCycleTime(part, machine, cal) {
    machine = machine || DEFAULT_MACHINE;
    cal = cal || { cut_factor: 1, overhead_min: 0, rounding_step_min: 0 };
    const cd = machine.cutting_data[String(part.material || "").toUpperCase()] || machine.cutting_data.CS;
    const maxRpm = machine.max_rpm;
    const envelope = barEnvelopeDia(part);
    const ops = [];
    const noncut = [];
    const warnings = [];
    const ends = ["A", "B"].slice(0, Math.max(1, machine.setups_per_part));

    for (const end of ends) {
      const feats = part.features.filter((f) => f.end === end);
      const turns = feats.filter((f) => f.type === "turn" || f.type === "taper_ext");

      const face = cd.face;
      const facePasses = Math.max(1, Math.ceil(machine.facing_stock_per_side_mm / face.doc));
      const faceT = facePasses * cutSeconds(envelope / 2, face.feed, rpm(envelope / 2, face.vc, maxRpm));
      ops.push({ end, operation: "face end", tool: "OD turning", cut_s: faceT,
        detail: `${facePasses} pass(es) over Ø${envelope.toFixed(1)}` });

      for (const f of feats) {
        const tool = toolFor(f);
        const q = f.qty;
        if (f.type === "turn" || f.type === "taper_ext") {
          const tc = cd.turn;
          const parent = parentOd(f, turns, envelope);
          const stock = Math.max(0, (parent - f.dia) / 2);
          const rough = stock > 0 ? Math.ceil(stock / tc.doc) : 0;
          let t = rough * cutSeconds(f.length + 1, tc.feed, rpm((parent + f.dia) / 2, tc.vc, maxRpm));
          t += cutSeconds(f.length, tc.finish_feed, rpm(f.dia, tc.vc, maxRpm));
          ops.push({ end, operation: `turn Ø${fmt(f.dia)} x ${fmt(f.length)}`, tool, cut_s: q * t,
            detail: `${rough} rough + 1 finish, from Ø${parent.toFixed(1)}` });
        } else if (f.type === "thread_ext" || f.type === "thread_int") {
          const th = cd.thread;
          const pitch = f.pitch || 1.5;
          const depth = (f.type === "thread_ext" ? 0.6134 : 0.5413) * pitch;
          const passes = Math.ceil(depth / th.infeed_per_pass) + Math.trunc(th.spring_passes);
          const r = rpm(f.dia, th.vc, maxRpm);
          const run = f.length + 2 * pitch + 2;
          const t = passes * (cutSeconds(run, pitch, r) + th.return_s_per_pass);
          ops.push({ end, operation: `thread Ø${fmt(f.dia)} x P${fmt(pitch)} x ${fmt(f.length)}`, tool,
            cut_s: q * t, detail: `${passes} passes` });
        } else if (f.type === "groove") {
          const g = cd.groove;
          const parent = parentOd(f, turns, envelope);
          const depth = Math.max(0.3, (parent - f.dia) / 2);
          const plunges = Math.max(1, Math.ceil(f.length / GROOVE_TOOL_WIDTH_MM));
          const t = plunges * (cutSeconds(depth, g.feed, rpm(f.dia, g.vc, maxRpm)) + 0.5);
          ops.push({ end, operation: `groove Ø${fmt(f.dia)} x ${fmt(f.length)}`, tool, cut_s: q * t,
            detail: `${plunges} plunge(s), ${depth.toFixed(2)} deep` });
        } else if (f.type === "drill") {
          const d = cd.drill;
          const feed = d.feed_per_mm_dia * f.dia;
          const travel = f.length + 0.3 * f.dia + 2;
          let t = cutSeconds(travel, feed, rpm(f.dia, d.vc, maxRpm));
          if (f.length / f.dia > d.peck_ratio) t *= d.peck_penalty;
          ops.push({ end, operation: `drill Ø${fmt(f.dia)} x ${fmt(f.length)}`, tool, cut_s: q * t,
            detail: `f=${feed.toFixed(3)} mm/rev` });
        } else if (f.type === "bore") {
          const b = cd.bore;
          const pre = preHole(f, part);
          const stock = Math.max(0, (f.dia - pre) / 2);
          const passes = Math.max(1, Math.ceil(stock / b.doc));
          const t = passes * cutSeconds(f.length + 1, b.feed, rpm(f.dia, b.vc, maxRpm));
          ops.push({ end, operation: `bore Ø${fmt(f.dia)} x ${fmt(f.length)}`, tool, cut_s: q * t,
            detail: `${passes} pass(es) from Ø${fmt(pre)}` });
        } else if (f.type === "cone_24") {
          const b = cd.bore;
          const pre = preHole(f, part);
          const radial = Math.max(0.2, (f.dia - pre) / 2);
          const coneLen = radial / Math.tan(CONE_HALF_ANGLE_DEG * Math.PI / 180);
          const passes = Math.max(1, Math.ceil(radial / b.doc)) + 1;
          const t = passes * cutSeconds(coneLen + 0.5, b.feed, rpm(f.dia, b.vc, maxRpm));
          ops.push({ end, operation: `24° cone Ø${fmt(f.dia)}`, tool, cut_s: q * t,
            detail: `${passes} passes, ${coneLen.toFixed(1)} long` });
        } else if (f.type === "chamfer") {
          ops.push({ end, operation: `chamfer Ø${fmt(f.dia)}`, tool, cut_s: q * cd.chamfer.seconds_each, detail: "" });
        } else if (f.type === "hex_chamfer") {
          ops.push({ end, operation: "hex chamfer", tool, cut_s: q * cd.hex_chamfer.seconds_each, detail: "" });
        }
      }

      const tools = new Set(["OD turning", ...feats.map(toolFor)]);
      noncut.push({ end, operation: "tool changes + approach", tool: `${tools.size} tools`,
        cut_s: tools.size * (machine.tool_index_s + machine.approach_retract_s_per_tool),
        detail: [...tools].sort().join(", ") });
      noncut.push({ end, operation: "load / unload + spindle", tool: "-",
        cut_s: machine.load_unload_s_per_setup + machine.spindle_start_stop_s_per_setup, detail: "" });
      if (tools.size > machine.turret_stations) {
        warnings.push(`Setup ${end} needs ${tools.size} tools but the turret has ${machine.turret_stations} stations.`);
      }
    }

    const cutting_s = ops.reduce((s, o) => s + o.cut_s, 0);
    const noncut_s = noncut.reduce((s, o) => s + o.cut_s, 0);
    const physics_min = (cutting_s + noncut_s) / 60;
    const cycle_time_min = cal.cut_factor * physics_min + cal.overhead_min;
    const step = cal.rounding_step_min;
    const rounded_min = step ? Math.round(Math.round(cycle_time_min / step) * step * 1e4) / 1e4 : cycle_time_min;
    return {
      operations: ops, noncut, warnings, cutting_s, noncut_s, physics_min, cycle_time_min, rounded_min,
      cut_factor: cal.cut_factor, overhead_min: cal.overhead_min,
    };
  }

  function fit(physics, actual) {
    const n = physics.length;
    if (n >= MIN_POINTS_FOR_TWO_PARAMS) {
      const mx = physics.reduce((a, b) => a + b, 0) / n;
      const my = actual.reduce((a, b) => a + b, 0) / n;
      let sxy = 0, sxx = 0;
      for (let i = 0; i < n; i++) { sxy += (physics[i] - mx) * (actual[i] - my); sxx += (physics[i] - mx) ** 2; }
      if (sxx > 0) {
        const k = sxy / sxx;
        const h = my - k * mx;
        if (k > 0 && h >= 0) return { k, h, mode: "factor + overhead" };
      }
    }
    let pa = 0, pp = 0;
    for (let i = 0; i < n; i++) { pa += physics[i] * actual[i]; pp += physics[i] ** 2; }
    return { k: pa / pp, h: 0, mode: "factor only" };
  }

  const r3 = (x) => Math.round(x * 1000) / 1000;

  /** refs: [{part, actual_cycle_time_min, use_for_calibration}] */
  function calibrate(refs, machine, roundingStep) {
    const step = roundingStep == null ? 0.25 : roundingStep;
    const used = refs.filter((r) => r.use_for_calibration !== false && r.actual_cycle_time_min > 0);
    if (!used.length) return { ...DEFAULT_CALIBRATION, rounding_step_min: step };
    const physics = used.map((r) => estimateCycleTime(r.part, machine).physics_min);
    const actual = used.map((r) => r.actual_cycle_time_min);
    const { k, h, mode } = fit(physics, actual);
    const residuals = [];
    const loo = [];
    const base = [];
    used.forEach((r, i) => {
      const pred = k * physics[i] + h;
      let looPred = null;
      if (used.length > 1) {
        const ph = physics.filter((_, j) => j !== i);
        const ac = actual.filter((_, j) => j !== i);
        const f = fit(ph, ac);
        looPred = f.k * physics[i] + f.h;
        loo.push(Math.abs(looPred - actual[i]));
        base.push(Math.abs(ac.reduce((a, b) => a + b, 0) / ac.length - actual[i]));
      }
      residuals.push({ description: r.part.description, actual_min: actual[i], physics_min: r3(physics[i]),
        predicted_min: r3(pred), loo_predicted_min: looPred == null ? null : r3(looPred) });
    });
    const mean = (a) => a.reduce((x, y) => x + y, 0) / a.length;
    return {
      cut_factor: Math.round(k * 1e4) / 1e4, overhead_min: Math.round(h * 1e4) / 1e4,
      rounding_step_min: step, mode, n_parts: used.length,
      mae_min: r3(mean(physics.map((p, i) => Math.abs(k * p + h - actual[i])))),
      loo_mae_min: loo.length ? r3(mean(loo)) : null,
      baseline_loo_mae_min: base.length ? r3(mean(base)) : null,
      residuals,
    };
  }

  const DEFAULT_COST = {
    quantity: 5, raw_material_rate: 85, caps: 0, crimp_n: 0, nut_red_n: 0, ferrule_wn: 0,
    cn: 0, was: 0, o_ring: 0, sr_ring: 0, margin: 1.3,
  };

  /** The 'Straight' sheet, formula for formula (S and T stay out of Net unless asked). */
  function computeCost(part, cost, cycleTimeMin, machiningRate, includeNutFerrule) {
    const c = { ...DEFAULT_COST, ...cost };
    const y = part.bar_size;
    const hex = y / 10;
    const tl = (part.overall_length + 6) / 10;
    const area = part.shape === "S" ? 0.8666 : Math.PI / 4;
    const weight = area * 7.85 * hex * hex * tl / 1000;
    const rmCost = weight * c.raw_material_rate;
    const cutting = y * 0.06;
    const machining = cycleTimeMin * (machiningRate == null ? 3.75 : machiningRate);
    const plating = weight * 0.6 * 24;
    let boughtOut = c.caps + c.crimp_n + c.cn + c.was + c.o_ring + c.sr_ring;
    if (includeNutFerrule) boughtOut += c.nut_red_n + c.ferrule_wn;
    const net = rmCost + cutting + machining + plating + boughtOut;
    const sp = net * c.margin;
    return { hex_cm: hex, tl_cm: tl, rm_weight_kg: weight, rm_cost: rmCost, cutting, machining, plating,
      bought_out: boughtOut, net, selling_price: sp, total_value: sp * c.quantity };
  }

  const HEADERS = [null, "Description", "Quantity", "RM", "Shape", "Y", "L", "Hex", "Tl",
    "Raw Material Weight-Str", "Raw Material Rate", "Raw Mmaterial Rate", "Cutting Price",
    "Cycle Time", "Machining Rate", "Plating", "Caps", "Crimp N", "NUT|RED N", "FERULE/WN",
    "CN", "Was", "O Ring", "SR Ring", "Net", "Mgn", "S.P."];

  /**
   * Build the quotation workbook with SheetJS (XLSX global). lines:
   * [{part, cost, cycle_time_min, cycle_time_source, estimate}]
   */
  function buildWorkbook(XLSX, lines, machiningRate) {
    const rate = machiningRate == null ? 3.75 : machiningRate;
    const ws = {};
    const put = (ref, cell) => { ws[ref] = cell; };
    HEADERS.forEach((h, i) => { if (h) put(XLSX.utils.encode_cell({ r: 0, c: i }), { t: "s", v: h }); });
    lines.forEach((line, i) => {
      const r = i + 2;
      const p = line.part;
      const c = { ...DEFAULT_COST, ...line.cost };
      const area = p.shape === "R" ? "PI()/4" : "0.8666";
      const vals = {
        A: i + 1, B: p.description, C: c.quantity, D: p.material, E: p.shape, F: p.bar_size,
        G: p.overall_length, K: c.raw_material_rate, N: Math.round(line.cycle_time_min * 100) / 100,
        Q: c.caps, R: c.crimp_n, S: c.nut_red_n, T: c.ferrule_wn, U: c.cn, V: c.was, W: c.o_ring,
        X: c.sr_ring, Z: c.margin,
      };
      for (const [col, v] of Object.entries(vals)) {
        put(`${col}${r}`, typeof v === "number" ? { t: "n", v } : { t: "s", v: String(v) });
      }
      const f = {
        H: `+F${r}/10`, I: `(+G${r}+6)/10`, J: `${area}*7.85*H${r}*H${r}*I${r}/1000`, L: `+J${r}*K${r}`,
        M: `+F${r}*0.06`, O: `+N${r}*${rate}`, P: `(J${r}*0.6)*24`,
        Y: `+L${r}+M${r}+O${r}+P${r}+Q${r}+R${r}+U${r}+V${r}+W${r}+X${r}`, AA: `+Y${r}*Z${r}`,
      };
      const cost = computeCost(p, c, line.cycle_time_min, rate, false);
      const cached = { H: cost.hex_cm, I: cost.tl_cm, J: cost.rm_weight_kg, L: cost.rm_cost, M: cost.cutting,
        O: cost.machining, P: cost.plating, Y: cost.net, AA: cost.selling_price };
      for (const [col, formula] of Object.entries(f)) put(`${col}${r}`, { t: "n", f: formula, v: cached[col] });
    });
    ws["!ref"] = `A1:AA${Math.max(1, lines.length + 1)}`;
    ws["!cols"] = HEADERS.map((_, i) => ({ wch: i === 1 ? 28 : 11 }));

    const rows = [["Part", "End", "Operation", "Tool", "Seconds", "Detail"]];
    for (const line of lines) {
      const e = line.estimate;
      if (!e) { rows.push([line.part.description, "", "cycle time entered manually", "", "", ""]); continue; }
      for (const o of [...e.operations, ...e.noncut]) {
        rows.push([line.part.description, o.end, o.operation, o.tool, Math.round(o.cut_s * 10) / 10, o.detail]);
      }
      rows.push([line.part.description, "", "physics machine time (min)", "", "", r3(e.physics_min)]);
      rows.push([line.part.description, "", "calibrated cycle time (min)", "", "",
        `${e.cut_factor} x physics + ${e.overhead_min} = ${e.cycle_time_min.toFixed(2)}`]);
      rows.push([]);
    }
    const bd = XLSX.utils.aoa_to_sheet(rows);
    bd["!cols"] = [{ wch: 28 }, { wch: 5 }, { wch: 34 }, { wch: 16 }, { wch: 9 }, { wch: 50 }];

    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, "Straight");
    XLSX.utils.book_append_sheet(wb, bd, "TL breakdown");
    return XLSX.write(wb, { type: "array", bookType: "xlsx" });
  }

  const EXTRACTION_RULES = `You are a process-planning engineer at Hydromatik, Belgaum, a maker of hydraulic tube fittings (DIN 2353 / ISO 8434-1 24-degree cone fittings). Parts are turned from carbon-steel hex bar billets on a CNC lathe in two setups: end A, then the part is flipped and end B is machined.

Read the drawing and describe the part as machining features so its cycle time can be estimated. Conventions:
- description: the part name from the title block exactly as written (e.g. "GE 12 X 3/8-L-B").
- drawing_no: the drawing number from the title block.
- material: "CS" for carbon steel (CS / CS. / EN / SAE10xx); otherwise the code as written.
- shape: "S" for hexagon bar (hex end view), "R" for round bar.
- bar_size: hex across-flats in mm from the end view (the plain hex size, e.g. 19, 22). For round bar, the bar diameter.
- overall_length: overall length of the part in mm.
- End A is the port / stud end (BSP, metric or UNF stud thread, sealing face, ED seal). End B is the tube end (24-degree cone and its nut thread). For symmetric unions pick either.
- turn: one feature per distinct turned outside diameter (thread OD, collars, sealing spigots, male-cone body), with the axial length from the end face or hex face.
- thread_ext / thread_int: dia = nominal MAJOR diameter, pitch in mm, length = full-thread length. Pitch diameters printed in brackets such as (Ø12.20/12.25) are gauging sizes, not the OD. BSP (G) majors and pitches: G1/8 9.728 P0.907, G1/4 13.157 P1.337, G3/8 16.662 P1.337, G1/2 20.955 P1.814, G3/4 26.441 P1.814, G1 33.249 P2.309. Metric "M18X1.5" is dia 18, pitch 1.5. Also add a matching "turn" feature for the thread OD.
- groove: undercuts, thread reliefs, O-ring grooves and seal grooves; dia = root diameter, length = groove width.
- drill: the through hole (usually drilled from end A, length = overall length) and any blind drilled holes.
- bore: counterbores and tube bores (e.g. Ø10.15/10.24 tube stop bore); dia = mean of the tolerance band, length = depth.
- cone_24: an internal 24-degree tube cone; dia = mouth diameter of the cone.
- taper_ext: an external taper or male 24-degree cone; dia = larger diameter, length = axial length.
- chamfer: one per chamfered edge on a turned diameter (e.g. 1.5 X 30 deg at the thread start).
- hex_chamfer: one per end where the hex corners are chamfered (15 or 30 deg marks on the hex).
- Use the dimension text provided with the drawing to get exact numbers. Do not invent features that are not on the drawing. When something is unreadable or ambiguous, make the most likely engineering assumption and add a short note to warnings.`;

  const EXTRACTION_FORMAT = `Reply with only one JSON object of this shape (numbers in mm):
{"description": string, "drawing_no": string, "material": string, "shape": "S" | "R", "bar_size": number, "overall_length": number,
 "features": [{"end": "A" | "B", "type": one of ${JSON.stringify(FEATURE_TYPES)}, "dia": number, "length": number, "pitch": number, "qty": integer, "note": string}],
 "warnings": [string]}
Example feature: {"end": "B", "type": "thread_ext", "dia": 18, "length": 8, "pitch": 1.5, "qty": 1, "note": "M18x1.5"}`;

  function extractionPrompt(filename, pdfText, imageNote) {
    let s = `${EXTRACTION_RULES}\n\nDrawing file: ${filename}. ${imageNote}\n`;
    if (pdfText && pdfText.trim()) {
      s += `\nDimension text extracted from the PDF (unordered):\n${pdfText.slice(0, 20000)}\n`;
    }
    return `${s}\n${EXTRACTION_FORMAT}`;
  }

  return {
    FEATURE_TYPES, DEFAULT_MACHINE, DEFAULT_CALIBRATION, DEFAULT_COST, MIN_POINTS_FOR_TWO_PARAMS,
    normalizePart, estimateCycleTime, calibrate, computeCost, buildWorkbook, extractionPrompt,
  };
});
