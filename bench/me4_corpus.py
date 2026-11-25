"""A fictional practice corpus for ME4, with gold questions computed from the same facts.

ME4 asks questions about a zip of mixed documents given on the checking day. This builds a stand-in
so retrieval, parsing, and answers can be measured before then. Everything is made up: a freight
cooperative called Bagwis Freight, its handbook, report, board deck, incident review, a scanned
memo, a shipments table, and a fleet workbook. The facts come from a seeded random generator, so
the same seed always gives the same files and the same answers, and no model can know them in
advance.

Each question has a kind (what it tests), a gold answer, and gold references (file, and page,
slide, or sheet where one applies). ``bench/me4_eval.py`` scores runs against them.

    uv run python -m bench.me4_corpus                       # data/me4_eval/documents/me4, ~60 questions
    uv run python -m bench.me4_corpus --rows 200000 --filler-mb 300   # bigger, for ingest timing

The output lives under data/, which is never committed.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import random
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path

from harness.config import REPO_ROOT

DEFAULT_OUT = REPO_ROOT / "data" / "me4_eval" / "documents" / "me4"
DEFAULT_QUESTIONS = REPO_ROOT / "data" / "me4_eval" / "me4_questions.json"  # outside documents, never indexed
COMPANY = "Bagwis Freight Cooperative"
HUBS = ["Cebu", "Davao", "Iloilo", "Cagayan de Oro", "Batangas", "Tacloban"]
REGIONS = ["Luzon", "Visayas", "Mindanao", "Metro Manila"]
MONTHS = ["July", "August", "September"]
YEAR_MONTHS = ["January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December"]  # fmt: skip
STATUSES = ["delivered", "delivered", "delivered", "delivered", "delayed", "returned"]
TRUCK_MODELS = ["Isuzu Forward", "Hino 500", "Fuso Fighter", "Isuzu Giga"]
ROLES = ["driver", "dispatcher", "mechanic", "warehouse staff"]


@dataclass
class Ref:
    file: str  # path relative to the corpus root
    page: int | None = None  # 1-based PDF page
    slide: int | None = None  # 1-based slide
    sheet: str | None = None  # workbook sheet


@dataclass
class Question:
    id: str
    kind: str
    question: str
    answer: str  # what a correct reply must say
    number: float | None = None  # when the answer is a number, its value
    tolerance: float = 0.0  # relative, for numbers that need a little room (web rates)
    refs: list[Ref] = field(default_factory=list)
    needs_web: bool = False


def peso(x: float) -> str:
    return f"PHP {x:,.0f}"


# Facts


def make_facts(rng: random.Random, rows: int) -> dict:
    f: dict = {}
    f["vacation_days"] = rng.choice([12, 14, 15, 18])
    f["vacation_days_2024"] = f["vacation_days"] - rng.choice([2, 3])
    f["sick_days"] = rng.choice([7, 8, 10])
    f["overtime_multiplier"] = rng.choice([1.25, 1.3, 1.5])
    f["per_diem"] = {r: rng.randrange(900, 2400, 50) for r in REGIONS}
    f["probation_months"] = rng.choice([3, 5, 6])
    f["revenue"] = {r: {m: rng.randrange(4_000_000, 19_000_000, 1000) for m in MONTHS} for r in REGIONS}
    f["ev_budget"] = rng.randrange(18_000_000, 42_000_000, 500_000)
    f["ev_trucks"] = {h: rng.randint(2, 9) for h in HUBS[:4]}
    f["ev_decision_date"] = date(2027, rng.randint(1, 4), rng.randint(1, 28))
    f["incident_hub"] = rng.choice(HUBS)
    f["incident_injuries"] = rng.randint(1, 4)
    f["incident_downtime_h"] = rng.randint(6, 40)
    f["incident_cause"] = rng.choice([
        "a worn brake line on a forklift that skipped its scheduled inspection",
        "an unsecured pallet stack that shifted during unloading",
        "a loading ramp hydraulic leak that was reported but not tagged out",
    ])  # fmt: skip
    f["incident_month"] = rng.choice(["March", "April", "May"])
    f["lease_rent"] = rng.randrange(380_000, 920_000, 5_000)
    f["lease_years"] = rng.choice([3, 5, 7])
    f["lease_site"] = rng.choice(["Mandaue", "Lapu-Lapu", "Talisay"])
    f["shipments"] = make_shipments(rng, rows)
    f["fleet"] = make_fleet(rng)
    return f


def make_shipments(rng: random.Random, rows: int) -> list[dict]:
    start = date(2026, 1, 1)
    out = []
    for i in range(rows):
        day = start + timedelta(days=rng.randrange(0, 334))
        origin = rng.choice(HUBS)
        dest = rng.choice([h for h in HUBS if h != origin])
        weight = rng.randrange(50, 12_000)
        out.append({
            "shipment_id": f"BF-{i + 100000}",
            "date": day.isoformat(),
            "origin_hub": origin,
            "dest_hub": dest,
            "weight_kg": weight,
            "status": rng.choice(STATUSES),
            "cost_php": round(weight * rng.uniform(4.5, 9.5), 2),
        })  # fmt: skip
    return out


def make_fleet(rng: random.Random) -> dict:
    trucks = [{"truck_id": f"T-{n:03d}", "hub": rng.choice(HUBS), "model": rng.choice(TRUCK_MODELS),
               "year": rng.randint(2012, 2026), "odometer_km": rng.randrange(8_000, 640_000, 100)}
              for n in range(1, 121)]  # fmt: skip
    first = ["Ana", "Ben", "Carla", "Dante", "Elena", "Fidel", "Grace", "Hector", "Ines", "Jun"]
    last = ["Reyes", "Santos", "Cruz", "Bautista", "Ocampo", "Garcia", "Mendoza", "Torres"]
    staff = [{"employee_id": f"E-{n:04d}", "name": f"{rng.choice(first)} {rng.choice(last)}",
              "role": rng.choice(ROLES), "hub": rng.choice(HUBS), "hired": f"{rng.randint(2010, 2026)}"}
             for n in range(1, 81)]  # fmt: skip
    return {"trucks": trucks, "staff": staff}


# Writers


def write_handbook(root: Path, f: dict) -> list[str]:
    per_diem = "\n".join(f"| {r} | {peso(v)} |" for r, v in f["per_diem"].items())
    text = f"""# {COMPANY} Employee Handbook

Effective January 2026. This edition replaces every earlier handbook.

## Leave

Regular employees earn {f["vacation_days"]} days of vacation leave and {f["sick_days"]} days of sick
leave each calendar year. Unused vacation leave carries over for one year only.

## Probation

New employees serve a probation period of {f["probation_months"]} months before regularization.

## Overtime

Work beyond eight hours a day is paid at {f["overtime_multiplier"]} times the hourly rate.

## Travel per diem

Drivers and staff on official trips receive a daily per diem by region.

| Region | Daily per diem |
|---|---|
{per_diem}
"""
    old = f"""# {COMPANY} Employee Handbook, 2024 edition

Superseded. Kept for reference only.

## Leave

Regular employees earn {f["vacation_days_2024"]} days of vacation leave each calendar year.
"""
    (root / "handbook").mkdir(parents=True, exist_ok=True)
    (root / "handbook" / "archive").mkdir(exist_ok=True)
    (root / "handbook" / "employee_handbook.md").write_text(text)
    (root / "handbook" / "archive" / "employee_handbook_2024.md").write_text(old)
    return ["handbook/employee_handbook.md", "handbook/archive/employee_handbook_2024.md"]


def write_report(root: Path, f: dict) -> str:
    from docx import Document

    doc = Document()
    doc.add_heading(f"{COMPANY}: Third Quarter 2026 Report", 0)
    doc.add_paragraph("Revenue by region for July to September 2026, in Philippine pesos. "
                      "Figures are unaudited.")  # fmt: skip
    table = doc.add_table(rows=1, cols=1 + len(MONTHS))
    for i, h in enumerate(["Region", *MONTHS]):
        table.rows[0].cells[i].text = h
    for r in REGIONS:
        cells = table.add_row().cells
        cells[0].text = r
        for i, m in enumerate(MONTHS, start=1):
            cells[i].text = f"{f['revenue'][r][m]:,}"
    doc.add_heading("Outlook", 1)
    doc.add_paragraph("The cooperative expects fourth quarter volume to follow the usual holiday peak.")
    (root / "finance").mkdir(parents=True, exist_ok=True)
    doc.save(root / "finance" / "q3_2026_report.docx")
    return "finance/q3_2026_report.docx"


def chart_png(values: dict[str, int], title: str) -> bytes:
    """A bar chart drawn with Pillow. Its numbers exist only as pixels, so only a vision pass reads them."""
    from PIL import Image, ImageDraw, ImageFont

    w, h = 1000, 600
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=26)
    small = ImageFont.load_default(size=22)
    d.text((40, 20), title, fill="black", font=font)
    top = max(values.values())
    bar_w = (w - 160) // len(values)
    for i, (label, v) in enumerate(values.items()):
        x0 = 100 + i * bar_w + 20
        bh = int((h - 200) * v / top)
        d.rectangle([x0, h - 100 - bh, x0 + bar_w - 40, h - 100], fill=(40, 110, 170))
        d.text((x0 + (bar_w - 40) // 2 - 8, h - 130 - bh), str(v), fill="black", font=font)
        d.text((x0, h - 85), label, fill="black", font=small)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def write_deck(root: Path, f: dict) -> str:
    from pptx import Presentation
    from pptx.util import Inches, Pt

    prs = Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[0])
    s.shapes.title.text = "Fleet Strategy 2027"
    s.placeholders[1].text = f"{COMPANY} board meeting, November 2026"

    s = prs.slides.add_slide(prs.slide_layouts[1])
    s.shapes.title.text = "Where we are"
    s.placeholders[1].text = (f"{len(f['fleet']['trucks'])} trucks across {len(HUBS)} hubs\n"
                              "Diesel costs rose through 2026\nCustomers ask for lower-emission delivery")  # fmt: skip

    s = prs.slides.add_slide(prs.slide_layouts[1])
    s.shapes.title.text = "Electric truck pilot"
    s.placeholders[1].text = (f"Pilot budget: {peso(f['ev_budget'])}\n"
                              "Charging at four hubs\nEighteen month evaluation")  # fmt: skip

    s = prs.slides.add_slide(prs.slide_layouts[5])
    s.shapes.title.text = "Pilot trucks per hub"
    png = io.BytesIO(chart_png(f["ev_trucks"], "Electric trucks planned per hub"))
    s.shapes.add_picture(png, Inches(0.8), Inches(1.6), width=Inches(8.4))

    s = prs.slides.add_slide(prs.slide_layouts[5])
    s.shapes.title.text = "Risks"
    box = s.shapes.add_textbox(Inches(0.8), Inches(1.8), Inches(8), Inches(3)).text_frame
    box.text = "Battery range on mountain routes"
    box.add_paragraph().text = "Grid reliability at provincial hubs"
    for p in box.paragraphs:
        p.font.size = Pt(24)

    s = prs.slides.add_slide(prs.slide_layouts[1])
    s.shapes.title.text = "Decision"
    s.placeholders[1].text = "The board will vote on the pilot after the risk review."
    d = f["ev_decision_date"]
    s.notes_slide.notes_text_frame.text = (
        f"Speaker note: the chair moved the final vote to {d:%B} {d.day}, {d.year}, "
        "so the risk review can include the typhoon season data."
    )
    (root / "board").mkdir(parents=True, exist_ok=True)
    prs.save(root / "board" / "fleet_strategy_2027.pptx")
    return "board/fleet_strategy_2027.pptx"


def write_incident_pdf(root: Path, f: dict) -> str:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import (
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    st = getSampleStyleSheet()
    out = root / "safety" / "incident_review_2026.pdf"
    out.parent.mkdir(parents=True, exist_ok=True)
    story = [
        Paragraph(f"{COMPANY}: Warehouse Incident Review", st["Title"]),
        Paragraph("Prepared by the Safety Committee. Internal.", st["Normal"]),
        Spacer(1, 12),
        Paragraph("This review covers one incident at a hub warehouse in 2026. Page two lists "
                  "the impact and page three the cause and actions.", st["Normal"]),
        PageBreak(),
        Paragraph("Impact", st["Heading1"]),
    ]
    rows = [["Item", "Value"], ["Hub", f["incident_hub"]], ["Month", f["incident_month"] + " 2026"],
            ["People injured", str(f["incident_injuries"])],
            ["Warehouse downtime (hours)", str(f["incident_downtime_h"])]]  # fmt: skip
    t = Table(rows, colWidths=[220, 160])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                           ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey)]))  # fmt: skip
    story += [t, PageBreak(), Paragraph("Cause and actions", st["Heading1"]),
              Paragraph(f"The root cause was {f['incident_cause']}.", st["Normal"]),
              Spacer(1, 8),
              Paragraph("All hubs will log forklift and ramp inspections in the maintenance system "
                        "and stop any equipment past its inspection date.", st["Normal"])]  # fmt: skip
    SimpleDocTemplate(str(out), pagesize=A4, title="Incident review").build(story)
    return "safety/incident_review_2026.pdf"


def write_scanned_memo(root: Path, f: dict, rng: random.Random) -> str:
    """A memo saved only as page images, slightly rotated, as a scanner would make it."""
    from PIL import Image, ImageDraw, ImageFont

    lines = [
        f"{COMPANY.upper()}",
        "MEMORANDUM",
        "",
        "To: Board of Directors",
        "From: Office of the General Manager",
        "Subject: Approval of new warehouse lease",
        "",
        f"The board approved a {f['lease_years']}-year lease for the new warehouse",
        f"in {f['lease_site']}, Cebu, at a monthly rent of {peso(f['lease_rent'])},",
        "inclusive of association dues and exclusive of VAT.",
        "",
        "Signed and approved.",
    ]
    font = ImageFont.load_default(size=30)
    pages = []
    for page_lines in (lines[:6], lines[6:]):
        img = Image.new("L", (1240, 1754), 250)
        d = ImageDraw.Draw(img)
        for i, line in enumerate(page_lines):
            d.text((120, 160 + i * 56), line, fill=30, font=font)
        pages.append(img.rotate(rng.uniform(-1.2, 1.2), expand=False, fillcolor=250).convert("RGB"))
    out = root / "archive" / "signed_lease_memo_scan.pdf"
    out.parent.mkdir(parents=True, exist_ok=True)
    pages[0].save(out, "PDF", resolution=150, save_all=True, append_images=pages[1:])
    return "archive/signed_lease_memo_scan.pdf"


def write_shipments(root: Path, f: dict) -> str:
    out = root / "data" / "shipments_2026.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(f["shipments"][0]))
        w.writeheader()
        w.writerows(f["shipments"])
    return "data/shipments_2026.csv"


def write_fleet(root: Path, f: dict) -> str:
    from openpyxl import Workbook

    wb = Workbook()
    for i, (name, rows) in enumerate(f["fleet"].items()):
        ws = wb.active if i == 0 else wb.create_sheet()
        ws.title = name
        ws.append(list(rows[0]))
        for r in rows:
            ws.append(list(r.values()))
    out = root / "data" / "fleet_register.xlsx"
    wb.save(out)
    return "data/fleet_register.xlsx"


def write_filler(root: Path, rng: random.Random, megabytes: float) -> int:
    """Meeting notes and older shipment logs on the same topics, so search has near misses to skip."""
    if megabytes <= 0:
        return 0
    target = int(megabytes * 1_000_000)
    made = written = 0
    notes = root / "filler" / "meeting_notes"
    notes.mkdir(parents=True, exist_ok=True)
    topics = ["fuel", "routes", "warehouse", "hiring", "training", "maintenance", "customers", "billing"]
    while written < target:
        if made % 10 == 9:  # every tenth file is a large old shipment log
            path = root / "filler" / f"shipments_archive_{made:05d}.csv"
            rows = make_shipments(rng, 20_000)
            for r in rows:
                r["date"] = r["date"].replace("2026", str(rng.randint(2019, 2024)))
            with path.open("w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)
        else:
            topic = rng.choice(topics)
            hub = rng.choice(HUBS)
            body = "\n".join(
                f"- {rng.choice(['Discussed', 'Reviewed', 'Agreed on', 'Deferred'])} {topic} at {hub} hub: "
                f"{rng.randint(2, 40)} items, next check in {rng.randint(1, 8)} weeks."
                for _ in range(rng.randint(30, 120))
            )  # fmt: skip
            path = notes / f"{2019 + made % 7}_{topic}_{hub.lower().replace(' ', '_')}_{made:05d}.md"
            path.write_text(f"# {topic.title()} meeting, {hub}\n\n{body}\n")
        written += path.stat().st_size
        made += 1
    return made


# Questions


def make_questions(f: dict, files: dict[str, str], rng: random.Random) -> list[Question]:
    q: list[Question] = []
    hb, rep, deck = files["handbook"], files["report"], files["deck"]
    pdf, memo, ship, fleet = files["incident"], files["memo"], files["shipments"], files["fleet"]

    def add(kind: str, text: str, answer: str, refs: list[Ref], number: float | None = None, **kw) -> None:
        q.append(Question(f"{kind}_{len(q) + 1:02d}", kind, text, answer, number, refs=refs, **kw))

    # Single lookups in markdown, including the archived edition that must not win.
    add("lookup", "How many vacation days do regular employees get?", f"{f['vacation_days']} days",
        [Ref(hb)], f["vacation_days"])  # fmt: skip
    add("recency", "Under the current handbook, how many vacation leave days does a regular employee earn?",
        f"{f['vacation_days']} days (the 2024 edition's {f['vacation_days_2024']} is superseded)",
        [Ref(hb)], f["vacation_days"])  # fmt: skip
    add("lookup", "How long is probation for new employees?", f"{f['probation_months']} months",
        [Ref(hb)], f["probation_months"])  # fmt: skip
    add("lookup", "What is the overtime rate?", f"{f['overtime_multiplier']} times the hourly rate",
        [Ref(hb)], f["overtime_multiplier"])  # fmt: skip
    for r in REGIONS:
        add("lookup_table", f"What is the daily per diem for a trip to {r}?", peso(f["per_diem"][r]),
            [Ref(hb)], f["per_diem"][r])  # fmt: skip
    r = rng.choice(REGIONS)
    days = rng.randint(2, 5)
    add("compute", f"What is the total per diem for a {days}-day trip to {r}?",
        peso(f["per_diem"][r] * days), [Ref(hb)], f["per_diem"][r] * days)  # fmt: skip

    # A Word table.
    for r in REGIONS:
        m = rng.choice(MONTHS)
        add("docx_table", f"What was {r}'s revenue in {m} 2026?", peso(f["revenue"][r][m]),
            [Ref(rep)], f["revenue"][r][m])  # fmt: skip
    total = sum(v for reg in f["revenue"].values() for v in reg.values())
    add("compute", "What was total revenue across all regions in the third quarter of 2026?", peso(total),
        [Ref(rep)], total)  # fmt: skip
    top = max(REGIONS, key=lambda x: f["revenue"][x]["September"])
    add("docx_table", "Which region had the highest revenue in September 2026?", top, [Ref(rep)])

    # The deck: body text, picture-only numbers, and speaker notes.
    add("pptx_text", "What is the budget for the electric truck pilot?", peso(f["ev_budget"]),
        [Ref(deck, slide=3)], f["ev_budget"])  # fmt: skip
    for hub, n in f["ev_trucks"].items():
        add("pptx_image", f"How many electric trucks are planned for the {hub} hub?", f"{n} trucks",
            [Ref(deck, slide=4)], n)  # fmt: skip
    d = f["ev_decision_date"]
    add("pptx_notes", "When will the board vote on the electric truck pilot?", f"{d:%B} {d.day}, {d.year}",
        [Ref(deck, slide=6)])  # fmt: skip

    # A text PDF with a table, and a scanned PDF.
    add("pdf_table", "How many people were injured in the warehouse incident?", str(f["incident_injuries"]),
        [Ref(pdf, page=2)], f["incident_injuries"])  # fmt: skip
    add("pdf_table", "How many hours was the warehouse down after the incident?",
        f"{f['incident_downtime_h']} hours", [Ref(pdf, page=2)], f["incident_downtime_h"])  # fmt: skip
    add("pdf_text", "What was the root cause of the warehouse incident?", f["incident_cause"], [Ref(pdf, page=3)])
    add("pdf_table", "At which hub did the warehouse incident happen?", f["incident_hub"], [Ref(pdf, page=2)])
    add("scanned", "What monthly rent did the board approve for the new warehouse lease?",
        peso(f["lease_rent"]), [Ref(memo, page=2)], f["lease_rent"])  # fmt: skip
    add("scanned", "How long is the new warehouse lease?", f"{f['lease_years']} years",
        [Ref(memo, page=2)], f["lease_years"])  # fmt: skip
    add("scanned", "Where is the new warehouse?", f"{f['lease_site']}, Cebu", [Ref(memo, page=2)])

    # Aggregation over the shipments table.
    by = defaultdict(int)
    cost = defaultdict(float)
    for s in f["shipments"]:
        month = YEAR_MONTHS[int(s["date"][5:7]) - 1]
        by[(s["origin_hub"], month, s["status"])] += 1
        cost[(s["origin_hub"], month)] += s["cost_php"]
    for _ in range(6):
        hub, month = rng.choice(HUBS), rng.choice(YEAR_MONTHS[:11])
        n = by[(hub, month, "delayed")]
        add("csv_agg", f"How many shipments from {hub} were delayed in {month} 2026?", str(n),
            [Ref(ship)], n)  # fmt: skip
    for _ in range(4):
        hub, month = rng.choice(HUBS), rng.choice(YEAR_MONTHS[:11])
        c = round(cost[(hub, month)])
        add("csv_agg", f"What was the total shipping cost for shipments from {hub} in {month} 2026?", peso(c),
            [Ref(ship)], c, tolerance=0.005)  # fmt: skip
    heavy = max(f["shipments"], key=lambda s: s["weight_kg"])
    add("csv_lookup", f"What is the status of shipment {heavy['shipment_id']}?", heavy["status"], [Ref(ship)])

    # The workbook.
    for _ in range(3):
        hub = rng.choice(HUBS)
        year = rng.choice([2016, 2018, 2020])
        n = sum(1 for t in f["fleet"]["trucks"] if t["hub"] == hub and t["year"] < year)
        add("xlsx_agg", f"How many trucks at the {hub} hub were made before {year}?", str(n),
            [Ref(fleet, sheet="trucks")], n)  # fmt: skip
    role = rng.choice(ROLES)
    n = sum(1 for s in f["fleet"]["staff"] if s["role"] == role)
    add("xlsx_agg", f"How many {role}s are on the staff list?", str(n), [Ref(fleet, sheet="staff")], n)

    # Across documents.
    n = sum(1 for t in f["fleet"]["trucks"] if t["hub"] == f["incident_hub"])
    add("multi_doc", "How many trucks are registered at the hub where the warehouse incident happened?", str(n),
        [Ref(pdf, page=2), Ref(fleet, sheet="trucks")], n)  # fmt: skip
    n = by[(f["incident_hub"], f["incident_month"], "delayed")]
    add("multi_doc", "In the month of the warehouse incident, how many shipments from that hub were delayed?",
        str(n), [Ref(pdf, page=2), Ref(ship)], n)  # fmt: skip
    ev_hub = max(f["ev_trucks"], key=f["ev_trucks"].get)
    n = sum(1 for t in f["fleet"]["trucks"] if t["hub"] == ev_hub)
    add("multi_doc", "The hub getting the most electric trucks in the pilot: how many trucks does it have now?",
        str(n), [Ref(deck, slide=4), Ref(fleet, sheet="trucks")], n)  # fmt: skip

    # Documents plus the web. The rate moves, so the number gets a wide band.
    usd = f["ev_budget"] / 58.0
    add("web", "Roughly how much is the electric truck pilot budget in US dollars at today's exchange rate?",
        f"about USD {usd:,.0f} ({peso(f['ev_budget'])} at roughly 58 pesos per dollar)",
        [Ref(deck, slide=3)], usd, tolerance=0.12, needs_web=True)  # fmt: skip
    usd = f["lease_rent"] * 12 / 58.0
    add("web", "What is a year of the new warehouse rent in US dollars at today's exchange rate?",
        f"about USD {usd:,.0f}", [Ref(memo, page=2)], usd, tolerance=0.12, needs_web=True)  # fmt: skip

    # Not in the documents: the agent must say so, not guess.
    for text in ["What is the dental benefit cap for employees?", "Who is the cooperative's external auditor?",
                 "What was revenue in the fourth quarter of 2026?"]:  # fmt: skip
        add("unanswerable", text, "not in the documents", [])
    return q


def build(out: Path, seed: int, rows: int, filler_mb: float, questions_path: Path = DEFAULT_QUESTIONS) -> dict:
    rng = random.Random(seed)
    out.mkdir(parents=True, exist_ok=True)
    facts = make_facts(rng, rows)
    handbook, _old = write_handbook(out, facts)
    files = {
        "handbook": handbook,
        "report": write_report(out, facts),
        "deck": write_deck(out, facts),
        "incident": write_incident_pdf(out, facts),
        "memo": write_scanned_memo(out, facts, rng),
        "shipments": write_shipments(out, facts),
        "fleet": write_fleet(out, facts),
    }
    filler = write_filler(out, random.Random(seed + 1), filler_mb)
    questions = make_questions(facts, files, random.Random(seed + 2))
    meta = {"seed": seed, "rows": rows, "filler_files": filler, "questions": [asdict(x) for x in questions]}
    questions_path.parent.mkdir(parents=True, exist_ok=True)
    questions_path.write_text(json.dumps(meta, indent=1))
    return meta


def corpus_size(root: Path) -> tuple[int, int]:
    files = [p for p in root.rglob("*") if p.is_file()]
    return len(files), sum(p.stat().st_size for p in files)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--rows", type=int, default=20_000, help="rows in shipments_2026.csv")
    ap.add_argument("--filler-mb", type=float, default=0, help="near-miss notes and old logs to add")
    args = ap.parse_args(argv)
    t0 = time.monotonic()
    meta = build(args.out, args.seed, args.rows, args.filler_mb, args.questions)
    n, size = corpus_size(args.out)
    kinds = defaultdict(int)
    for x in meta["questions"]:
        kinds[x["kind"]] += 1
    print(f"{n} files, {size / 1e6:.1f} MB in {args.out} ({time.monotonic() - t0:.1f} s)")
    print(f"{len(meta['questions'])} questions in {args.questions}")
    print("  " + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
