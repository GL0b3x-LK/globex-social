"""Build Karen's PDF guide to the image bank.

Every name in it is read straight from the bank itself — the character
roster and pool.json — so the guide cannot drift out of step with what the
assistant will actually answer to. Re-run it whenever shots or people are
added; a guide listing a photograph nobody can ask for is worse than none.

Run:
    .venv/bin/python scripts/build_image_bank_guide.py
"""

from __future__ import annotations

import asyncio
import base64
import html
import io
import pathlib
import sys

from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(str(ROOT / ".env"), override=True)

from PIL import Image  # noqa: E402

from app.video import library  # noqa: E402
from app.workflows import asset_bank  # noqa: E402

OUT_DIR = ROOT / "docs"
OUT_DIR.mkdir(exist_ok=True)
PDF = OUT_DIR / "Globex - Image Bank Guide.pdf"

NAVY = "#002D70"
CYAN = "#5BC0DE"


def thumb(path: pathlib.Path, width: int = 300, quality: int = 72) -> str:
    """A small JPEG data URI — keeps the PDF light enough to send on WhatsApp."""
    with Image.open(path) as im:
        im = im.convert("RGB")
        ratio = width / im.width
        im = im.resize((width, max(1, int(im.height * ratio))), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=quality, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def logo_uri() -> str:
    for name in ("globex-lockup-side.png", "globex-wordmark-navy.png"):
        p = ROOT / "app" / "templates" / "assets" / "logos" / name
        if p.exists():
            return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()
    return ""


# --------------------------------------------------------------------------- #
# data
# --------------------------------------------------------------------------- #

people = [c for c in library.load_characters() if c.usable]

assets = [a for a in asset_bank.assets() if not a.file.startswith("placeholder")]

GROUPS: list[tuple[str, tuple[str, ...]]] = [
    ("Chicken", ("chicken", "whole-bird", "whole-chicken", "henfowl")),
    ("Duck & turkey", ("duck", "turkey")),
    ("Red meat", ("beef", "buffalo", "veal", "lamb", "mutton", "pork")),
    ("Fish & seafood", ("fish", "seafood")),
    ("Potato & vegetables", ("fries", "potato", "frozen-vegetables")),
    ("Grains, pulses & oils", ("grains", "pulses", "oil-seeds", "edible-oils", "feedstuff")),
    ("Offal", ("poultry-offal",)),
    ("Globex brand & packaging", ("globex", "brand-")),
]


def group_of(file: str) -> str:
    for title, keys in GROUPS:
        if any(k in file for k in keys):
            return title
    return "Other"


grouped: dict[str, list] = {}
for a in sorted(assets, key=lambda x: x.label):
    grouped.setdefault(group_of(a.file), []).append(a)

EXAMPLES = [
    (
        "A person holding a product",
        "Make me a post of Priya holding the lamb in the cold store",
        "Uses Priya's saved photo <b>and</b> our lamb photo together — so it is our Priya, "
        "and our lamb.",
    ),
    (
        "Just swap the picture",
        "Change the picture to the duck retail pack",
        "Drops that exact photo straight in. No AI, instant, and it looks precisely like the "
        "photo we own.",
    ),
    (
        "Pick which shot of a thing",
        "Use the beef QC hands shot instead",
        "Same product, different framing — see <b>Ways to say it</b> below.",
    ),
    (
        "A person on their own",
        "A post of John on the quality control floor",
        "Uses John's saved photo, puts him in the scene you asked for.",
    ),
    (
        "Something brand new",
        "Generate a brand new image of a shipping port at sunrise",
        "Say <b>generate</b>, <b>brand new</b> or <b>from scratch</b> and it will create one "
        "rather than use the bank.",
    ),
]

MODIFIERS = [
    ("hero", "the clean, front-and-centre product shot", "the lamb hero shot"),
    ("QC hands", "gloved hands inspecting it", "beef QC hands"),
    ("cold store", "on a pallet in the chilled warehouse", "duck carton cold store"),
    ("packed", "boxed and ready to ship", "chicken drumstick hero packed"),
    ("carton / retail", "which pack format, where there is more than one", "duck retail"),
]


# --------------------------------------------------------------------------- #
# page
# --------------------------------------------------------------------------- #


def esc(t: str) -> str:
    return html.escape(t)


people_cards = "\n".join(
    f"""<div class="card">
      <img src="{thumb(c.reference_dir / "front.jpg", 300)}" alt="">
      <div class="cap"><b>{esc(c.name)}</b><span>{esc(c.role)}</span>
      <em>say &ldquo;{esc(c.name)}&rdquo; or &ldquo;{esc(c.full_name)}&rdquo;</em></div>
    </div>"""
    for c in people
)

product_sections = []
for title, _keys in GROUPS + [("Other", ())]:
    items = grouped.get(title)
    if not items:
        continue
    cards = "\n".join(
        f"""<div class="pcard">
          <img src="{thumb(library.ASSET_POOL / a.file, 240)}" alt="">
          <span>{esc(a.label)}</span>
        </div>"""
        for a in items
    )
    product_sections.append(
        f'<div class="pblock"><h3 class="grp">{esc(title)} <i>({len(items)})</i></h3>'
        f'<div class="pgrid">{cards}</div></div>'
    )

example_rows = "\n".join(
    f"""<div class="ex">
      <div class="exhead">{esc(kind)}</div>
      <div class="bubble">{esc(say)}</div>
      <div class="note">{note}</div>
    </div>"""
    for kind, say, note in EXAMPLES
)

modifier_rows = "\n".join(
    f"<tr><td class='k'>{esc(word)}</td><td>{esc(means)}</td>"
    f"<td class='eg'>&ldquo;{esc(eg)}&rdquo;</td></tr>"
    for word, means, eg in MODIFIERS
)

HTML = f"""<!doctype html>
<html><head><meta charset="utf-8"><style>
  @page {{ size: A4; margin: 14mm 13mm; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, "Helvetica Neue", Arial, sans-serif;
         color: #16233a; margin: 0; font-size: 10.5pt; line-height: 1.45; }}
  h1 {{ color: {NAVY}; font-size: 23pt; margin: 0 0 4px; letter-spacing: -.5px; }}
  h2 {{ color: {NAVY}; font-size: 14pt; margin: 26px 0 10px; padding-bottom: 5px;
        border-bottom: 2px solid {CYAN}; }}
  h2:first-of-type {{ margin-top: 20px; }}
  h3.grp {{ font-size: 10.5pt; color: {NAVY}; margin: 14px 0 7px;
            text-transform: uppercase; letter-spacing: .09em; }}
  h3.grp i {{ color: #8a93a5; font-style: normal; font-weight: 400; }}
  /* a category is read as a block — splitting one across a page break left a
     stray two-item page at the end */
  .pblock {{ break-inside: avoid; page-break-inside: avoid; }}
  .lede {{ color: #4a5568; font-size: 11pt; margin: 0 0 4px; }}
  header {{ display: flex; justify-content: space-between; align-items: flex-start;
            border-bottom: 3px solid {NAVY}; padding-bottom: 12px; }}
  header img {{ height: 34px; }}
  .steps {{ display: flex; gap: 10px; margin: 14px 0 0; }}
  .step {{ flex: 1; background: #f4f8fc; border-left: 3px solid {CYAN};
           padding: 9px 11px; font-size: 9.5pt; }}
  .step b {{ display: block; color: {NAVY}; }}

  .grid {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 9px; }}
  .card img {{ width: 100%; aspect-ratio: 1/1; object-fit: cover; border-radius: 5px;
               display: block; }}
  .cap {{ font-size: 8pt; line-height: 1.3; margin-top: 4px; }}
  .cap b {{ color: {NAVY}; font-size: 9.5pt; display: block; }}
  .cap span {{ display: block; color: #5a6478; }}
  .cap em {{ display: block; color: #8a93a5; font-style: normal; margin-top: 2px; }}

  .pgrid {{ display: grid; grid-template-columns: repeat(6, 1fr); gap: 7px; }}
  .pcard img {{ width: 100%; aspect-ratio: 1/1; object-fit: cover; border-radius: 4px;
                display: block; }}
  .pcard span {{ display: block; font-size: 7.4pt; line-height: 1.25; margin-top: 3px;
                 color: #35405a; }}

  .ex {{ margin-bottom: 11px; page-break-inside: avoid; }}
  .exhead {{ font-size: 8.5pt; text-transform: uppercase; letter-spacing: .09em;
             color: {NAVY}; font-weight: 700; margin-bottom: 3px; }}
  .bubble {{ background: {NAVY}; color: #fff; display: inline-block; padding: 7px 13px;
             border-radius: 14px 14px 14px 3px; font-size: 10pt; }}
  .note {{ color: #5a6478; font-size: 9.2pt; margin-top: 3px; }}

  table {{ width: 100%; border-collapse: collapse; font-size: 9.5pt; }}
  td {{ padding: 6px 8px; border-bottom: 1px solid #e3e8ef; vertical-align: top; }}
  td.k {{ color: {NAVY}; font-weight: 700; white-space: nowrap; width: 110px; }}
  td.eg {{ color: #8a93a5; font-style: italic; white-space: nowrap; }}

  .tip {{ background: #f4f8fc; border-left: 3px solid {NAVY}; padding: 10px 13px;
          font-size: 9.5pt; margin-top: 12px; }}
  .pagebreak {{ page-break-before: always; }}
  footer {{ margin-top: 22px; padding-top: 9px; border-top: 1px solid #e3e8ef;
            color: #8a93a5; font-size: 8pt; }}
</style></head><body>

<header>
  <div>
    <h1>Your image bank</h1>
    <p class="lede">{len(people)} people and {len(assets)} product photographs the
    assistant already owns &mdash; just name them.</p>
  </div>
  <img src="{logo_uri()}" alt="Globex International">
</header>

<div class="steps">
  <div class="step"><b>1. Name it</b>Say the person and&#47;or the product in your
  normal words.</div>
  <div class="step"><b>2. It finds them</b>It pulls the real photographs, not a
  made-up version.</div>
  <div class="step"><b>3. You approve</b>Nothing posts until you say
  <i>approve</i>, as always.</div>
</div>

<h2>How to ask</h2>
{example_rows}

<div class="tip"><b>Why this matters:</b> when you name someone we have a photo of,
you get <i>that</i> person every time &mdash; same face, same uniform, in every post.
The assistant is placing real photographs, not inventing people and products from
a description.</div>

<h2>Ways to say it</h2>
<table>{modifier_rows}</table>

<div class="pagebreak"></div>
<h2>The people ({len(people)})</h2>
<div class="grid">{people_cards}</div>

<div class="tip">Every one of these is an approved, made-up character &mdash; not a
real employee. You can use a first name (&ldquo;Priya&rdquo;), a full name
(&ldquo;Priya Nair&rdquo;) or a surname (&ldquo;Nair&rdquo;).</div>

<div class="pagebreak"></div>
<h2>The products ({len(assets)})</h2>
<p class="lede" style="font-size:10pt">Say any of these names. Add
<i>hero</i>, <i>QC hands</i>, <i>cold store</i> or <i>packed</i> to choose the shot.</p>
{"".join(product_sections)}

<footer>Globex International &middot; social media assistant &middot; ask on WhatsApp
for anything not listed here and it will generate it instead.</footer>

</body></html>"""


async def main() -> None:
    from playwright.async_api import async_playwright

    src = OUT_DIR / "_image_bank_guide.html"
    src.write_text(HTML, encoding="utf-8")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.goto(src.as_uri())
        await page.pdf(path=str(PDF), format="A4", print_background=True)
        await browser.close()
    src.unlink()
    print(f"wrote {PDF}  ({PDF.stat().st_size / 1024:.0f} KB)")


asyncio.run(main())
