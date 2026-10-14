"""Read a fitting drawing (PDF or image) with Claude and return a PartSpec.

The PDF is sent as a document block (Claude sees the rendered drawing) together
with the vector text pulled out of the PDF, which gives exact dimension strings
to cross-check against. The response is constrained to the PartSpec schema
with structured outputs, so it can go straight into the cycle-time engine.
The result must still be reviewed by a person before quoting.
"""

from __future__ import annotations

import base64
from pathlib import Path

import anthropic

from .models import PartSpec

MODEL = "claude-opus-5-5"

SYSTEM_PROMPT = """\
You are a process-planning engineer at Hydromatik, Belgaum, a maker of hydraulic \
tube fittings (DIN 2353 / ISO 8434-1 24-degree cone fittings). Parts are turned from \
carbon-steel hex bar billets on a CNC lathe in two setups: end A, then the part is \
flipped and end B is machined.

Read the drawing and describe the part as machining features so its cycle time can \
be estimated. Conventions:

- description: the part name from the title block exactly as written (e.g. "GE 12 X 3/8-L-B").
- drawing_no: the drawing number from the title block.
- material: "CS" for carbon steel (CS / CS. / EN / SAE10xx); otherwise the code as written.
- shape: "S" for hexagon bar (hex end view), "R" for round bar.
- bar_size: hex across-flats in mm from the end view (the plain hex size, e.g. 19, 22). \
For round bar, the bar diameter.
- overall_length: overall length of the part in mm.
- End A is the port / stud end (BSP, metric or UNF stud thread, sealing face, ED seal). \
End B is the tube end (24-degree cone and its nut thread). For symmetric unions pick either.
- turn: one feature per distinct turned outside diameter (thread OD, collars, sealing \
spigots, male-cone body), with the axial length from the end face or hex face.
- thread_ext / thread_int: dia = nominal MAJOR diameter, pitch in mm, length = full-thread \
length. Pitch diameters printed in brackets such as (Ø12.20/12.25) are gauging sizes, not \
the OD. BSP (G) majors and pitches: G1/8 9.728 P0.907, G1/4 13.157 P1.337, G3/8 16.662 \
P1.337, G1/2 20.955 P1.814, G3/4 26.441 P1.814, G1 33.249 P2.309. Metric "M18X1.5" is dia \
18, pitch 1.5. Also add a matching "turn" feature for the thread OD.
- groove: undercuts, thread reliefs, O-ring grooves and seal grooves, dia = root diameter, \
length = groove width.
- drill: the through hole (usually drilled from end A, length = overall length) and any \
blind drilled holes.
- bore: counterbores and tube bores (e.g. Ø10.15/10.24 tube stop bore), dia = mean of the \
tolerance band, length = depth.
- cone_24: an internal 24-degree tube cone; dia = mouth diameter of the cone.
- taper_ext: an external taper or male 24-degree cone; dia = larger diameter, length = axial length.
- chamfer: one per chamfered edge on a turned diameter (e.g. 1.5 X 30 deg at the thread start).
- hex_chamfer: one per end where the hex corners are chamfered (15 or 30 deg marks on the hex).
- Use the dimension text provided with the drawing to get exact numbers. Do not invent \
features that are not on the drawing. When something is unreadable or ambiguous, make the \
most likely engineering assumption and add a short note to warnings.
"""


def _pdf_text(pdf_bytes: bytes) -> str:
    try:
        import pymupdf
    except ImportError:
        return ""
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        return "\n".join(page.get_text("text") for page in doc)


def _content_blocks(data: bytes, filename: str) -> list[dict]:
    suffix = Path(filename).suffix.lower()
    b64 = base64.standard_b64encode(data).decode()
    if suffix == ".pdf":
        blocks = [{"type": "document",
                   "source": {"type": "base64", "media_type": "application/pdf", "data": b64}}]
        text = _pdf_text(data)
        if text.strip():
            blocks.append({"type": "text",
                           "text": "Dimension text extracted from the PDF (unordered):\n" + text})
        return blocks
    media = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
             ".webp": "image/webp", ".gif": "image/gif"}.get(suffix)
    if media is None:
        raise ValueError(f"Unsupported drawing format: {suffix} (use PDF, PNG or JPG)")
    return [{"type": "image", "source": {"type": "base64", "media_type": media, "data": b64}}]


class ExtractionError(RuntimeError):
    pass


def extract_part_from_drawing(
    data: bytes,
    filename: str,
    client: anthropic.Anthropic | None = None,
) -> PartSpec:
    """Send one drawing to Claude and return the parsed PartSpec."""
    client = client or anthropic.Anthropic()
    content = _content_blocks(data, filename) + [{
        "type": "text",
        "text": f"Drawing file: {filename}. Extract the part and its machining features.",
    }]
    try:
        response = client.beta.messages.parse(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            output_config={"effort": "high"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": content}],
            output_format=PartSpec,
        )
    except anthropic.AuthenticationError as e:
        raise ExtractionError("Claude API key missing or invalid (set ANTHROPIC_API_KEY).") from e
    except anthropic.RateLimitError as e:
        raise ExtractionError("Claude API rate limit reached, try again shortly.") from e
    except anthropic.APIStatusError as e:
        raise ExtractionError(f"Claude API error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise ExtractionError("Could not reach the Claude API (network).") from e

    if response.stop_reason == "refusal":
        raise ExtractionError("Claude declined to read this drawing.")
    if response.stop_reason == "max_tokens" or response.parsed_output is None:
        raise ExtractionError("Drawing extraction was incomplete; please retry or enter features manually.")
    return response.parsed_output
