"""A tender as a whole: what is left to do, queries for the architect, products
remembered from earlier tenders, and the quantities across every drawing."""

from __future__ import annotations

import io
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from django.urls import reverse

from .models import LibraryProduct, Premeasure, Product, Project, RoomCheck
from .setup import drawing_mapping, house_job, norm, unmapped, wastage_for


# --- rooms the estimator has checked ----------------------------------------------


def checks_for(pm: Premeasure) -> dict[int, RoomCheck]:
    return {c.room_id: c for c in pm.checks.all()}


def rooms_to_check(pm: Premeasure, floors: dict | None = None) -> list[dict]:
    """Rooms still marked to check that will go into Callidus (their finish has
    a product), biggest first, since they matter most. With no products chosen
    yet, every room marked to check."""
    if not pm.summary:
        return []
    if floors is None:
        floors, _ = drawing_mapping(pm)
    done = checks_for(pm)
    flagged = [r for r in pm.summary["rooms"] if r["status"] != "Verified" and r["id"] not in done]
    priced = [r for r in flagged if r["finish"] in floors]
    todo = priced if floors else flagged
    return sorted(todo, key=lambda r: -r["area_m2"])


def written_out_of_date(pm: Premeasure, floors: dict, skirtings: dict) -> bool:
    """Written files that no longer match the products or room checks."""
    if not (pm.output and pm.status == Premeasure.Status.DONE):
        return False
    if (floors, skirtings) != (pm.finish_products, pm.skirting_products):
        return True
    at = (pm.written or {}).get("at")
    last = pm.checks.order_by("-at").values_list("at", flat=True).first()
    return bool(last and (not at or last.isoformat() > at))


def complete(pm: Premeasure) -> bool:
    """Every finish found on the drawing has a product."""
    if not pm.summary:
        return False
    floors, skirtings = drawing_mapping(pm)
    return bool(floors) and not unmapped(pm.summary, floors, skirtings)


# --- the to-do list -------------------------------------------------------------


@dataclass
class Todo:
    text: str
    url: str = ""
    button: str = ""
    level: str = "do"  # do (needs the estimator), wait (the software is busy), info
    post: bool = False  # the button submits a form


def tender_todo(project: Project, user=None) -> list[Todo]:
    """What is left before the tender's quantities are ready, in the order to do it."""
    items: list[Todo] = []
    drawings = list(project.premeasures.select_related("drawing").prefetch_related("checks"))
    if house_job() is None and not project.uploads.filter(kind="est").exists():
        staff = bool(user and user.is_staff)
        items.append(Todo("There is no Callidus job to build the files on yet.",
                          reverse("settings_template") if staff else "",
                          "Add the Callidus template" if staff else "", "do"))
    if project.setup_step != Project.Step.DONE:
        items.append(Todo("The tender set-up is not finished.", reverse("wizard", args=[project.pk, project.setup_step]),
                          "Carry on setting up"))
    if not drawings:
        items.append(Todo("No drawings yet.", reverse("wizard", args=[project.pk, "drawings"]), "Add drawings"))
        return items

    busy = [d for d in drawings if d.busy]
    if busy:
        items.append(Todo(f"{len(busy)} drawing{_s(busy)} being read or written. This page updates when they finish.",
                          level="wait"))
    for d in drawings:
        if d.status == Premeasure.Status.FAILED:
            if d.needs_scale:
                what = "sheet size and scale" if d.needs_sheet else "scale"
                items.append(Todo(f"{d.title}: give the {what}.", reverse("premeasure", args=[d.pk]), f"Enter the {what}"))
            elif d.plain_error:
                items.append(Todo(f"{d.title}: cannot be read. {d.error.split('. ')[0]}.",
                                  reverse("premeasure", args=[d.pk]), "See why"))
            else:
                items.append(Todo(f"{d.title}: something went wrong.", reverse("premeasure", args=[d.pk]), "Try again"))

    read = [d for d in drawings if d.summary and not d.busy]
    no_product = codes_without_product(project, read)
    if no_product:
        codes = ", ".join(sorted(no_product, key=natural)[:12]) + (" and more" if len(no_product) > 12 else "")
        items.append(Todo(f"{len(no_product)} finish code{_s(no_product)} on the drawings with no product: {codes}.",
                          reverse("wizard", args=[project.pk, "products"]), "Choose products"))

    n_check = sum(len(rooms_to_check(d)) for d in read)
    if n_check:
        items.append(Todo(f"{n_check} room{'s' if n_check != 1 else ''} to check.",
                          reverse("check_tender", args=[project.pk]), "Check rooms"))

    ready = [d for d in read if d.status == Premeasure.Status.REVIEW and complete(d)]
    stale = []
    for d in read:
        floors, skirtings = drawing_mapping(d)
        if written_out_of_date(d, floors, skirtings):
            stale.append(d)
    if ready or stale:
        bits = []
        if ready:
            bits.append(f"{len(ready)} drawing{_s(ready)} ready to write")
        if stale:
            bits.append(f"{len(stale)} written before the latest changes")
        items.append(Todo(", ".join(bits).capitalize() + ".", reverse("write_ready", args=[project.pk]),
                          "Write Callidus files", post=True))

    q = queries(project)
    if q:
        items.append(Todo(f"{len(q)} quer{'ies' if len(q) != 1 else 'y'} for the architect.",
                          reverse("queries", args=[project.pk]), "See the queries", level="info"))
    if read:
        items.append(Todo("Quantities for the whole tender, by product, with what is still to measure by hand.",
                          reverse("tender_quantities", args=[project.pk]), "Download quantities", level="info"))
    return items


def natural(code: str):
    """FF2 before FF10."""
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", code)]


def _s(items) -> str:
    return "s" if len(items) != 1 else ""


def codes_without_product(project: Project, drawings: list[Premeasure]) -> set[str]:
    have = {norm(p.finish_code) for p in project.products.exclude(library=None)}
    out = set()
    for d in drawings:
        floors, skirtings = drawing_mapping(d)
        for f in d.summary["finishes"]:
            chosen = floors if f["kind"] == "floor" else skirtings
            if norm(f["code"]) not in have and f["code"] not in chosen:
                out.add(f["code"])
    return out


# --- every finish code across the tender ------------------------------------------


@dataclass
class CodeUse:
    code: str
    kind: str
    descriptions: dict[str, list[str]] = field(default_factory=dict)  # text -> drawings
    amount: float = 0.0
    fill: list | None = None

    @property
    def conflict(self) -> bool:
        return len({_words(t) for t in self.descriptions}) > 1

    @property
    def drawings(self) -> list[str]:
        return sorted({d for ds in self.descriptions.values() for d in ds})


def _words(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def codes_in_tender(project: Project) -> dict[str, CodeUse]:
    """Each finish code on the tender's drawings: what each legend says, where, and how much."""
    out: dict[str, CodeUse] = {}
    for d in project.premeasures.exclude(summary=None):
        for f in d.summary["finishes"]:
            use = out.setdefault(norm(f["code"]), CodeUse(f["code"], f["kind"], fill=f.get("fill")))
            text = (f.get("description") or "").strip()
            if text:
                use.descriptions.setdefault(text, []).append(d.title)
            use.amount += f.get("amount") or 0
    return out


def suggest_from_history(project: Project) -> list[Product]:
    """Finish codes still without a product: use the product an earlier tender
    with the same architect (or client) gave the same code with the same legend
    text. Returns the rows filled."""
    filled = []
    peers = Project.objects.exclude(pk=project.pk)
    if project.architect_id:
        peers = peers.filter(architect_id=project.architect_id)
    elif project.client_id:
        peers = peers.filter(client_id=project.client_id)
    else:
        return filled
    earlier = {}
    for p in Product.objects.filter(project__in=peers).exclude(library=None).select_related("library", "project") \
            .order_by("-project__created_at"):
        earlier.setdefault((norm(p.finish_code), _words(p.description)), p)
    for row in project.products.filter(library=None):
        match = earlier.get((norm(row.finish_code), _words(row.description)))
        if match and match.library.active:
            row.library = match.library
            row.spec_source = f"As on {match.project.code} (same {'architect' if project.architect_id else 'client'})"[:200]
            row.save(update_fields=["library", "spec_source"])
            filled.append(row)
    return filled


# --- queries for the architect ----------------------------------------------------


@dataclass
class Query:
    drawing: str
    text: str


def queries(project: Project) -> list[Query]:
    """What the drawings leave open, worded for the architect."""
    out: list[Query] = []
    for d in project.premeasures.select_related("drawing"):
        name = f"{d.drawing.drawing_number or d.title}" + (f" (page {d.page})" if d.page > 1 else "")
        if d.status == Premeasure.Status.FAILED and d.plain_error:
            if "no finish legend" in d.error:
                out.append(Query(name, "There is no finish legend on this drawing. Please send the legend or "
                                       "the finishes schedule it refers to."))
            elif "picture" in d.error:
                out.append(Query(name, "This drawing was issued as a scanned image. Please send the PDF exported "
                                       "from the drawing software."))
            continue
        s = d.summary
        if not s:
            continue
        if s.get("picture"):
            out.append(Query(name, "This drawing was issued as a scanned image. Please send the PDF exported from "
                                   "the drawing software, so the areas can be measured exactly."))
            unclear = s["picture"].get("unclear_codes") or []
            if unclear:
                out.append(Query(name, f"Some finish codes are not legible on the scan ({', '.join(unclear)}). "
                                       "Please confirm the codes in the legend."))
        not_in_legend = [f["code"] for f in s["finishes"] if not f.get("in_legend", True)]
        not_in_legend += [c for c in (s.get("tagged_not_in_legend") or {}) if c not in not_in_legend]
        if not_in_legend:
            out.append(Query(name, f"{', '.join(not_in_legend)} {'is' if len(not_in_legend) == 1 else 'are'} used "
                                   "on the plan but not in the legend. Please confirm the finish."))
        big = [u for u in s.get("unknown_fills") or [] if u["area_m2"] >= 20]
        if big:
            area = sum(u["area_m2"] for u in big)
            out.append(Query(name, f"About {area:,.0f} m² of the plan is coloured in a way the legend does not "
                                   "explain. Please confirm the finish in those areas."))
        hand = s.get("manual_finishes") or []
        if hand:
            out.append(Query(name, f"{', '.join(hand)} {'is' if len(hand) == 1 else 'are'} shown by hatching or "
                                   "linework only. Please confirm the extent, or send a coloured version."))
    for use in codes_in_tender(project).values():
        if use.conflict:
            texts = "; ".join(f"{', '.join(ds)}: \"{t[:80]}\"" for t, ds in use.descriptions.items())
            out.append(Query("Across drawings", f"{use.code} is described differently on different drawings "
                                                f"({texts}). Please confirm which applies."))
    return out


def query_email(project: Project, items: list[Query]) -> tuple[str, str]:
    """Subject and body of an email to the architect with the queries."""
    who = project.architect.contact_name.split()[0] if project.architect and project.architect.contact_name else ""
    subject = f"{project.name}: queries on the floor finishes drawings"
    lines = [f"Hi {who}," if who else "Hi,", "",
             f"We are pricing the floor finishes for {project.name} and have a few queries on the drawings:", ""]
    by_drawing: dict[str, list[str]] = defaultdict(list)
    for q in items:
        by_drawing[q.drawing].append(q.text)
    n = 1
    for drawing, texts in by_drawing.items():
        lines.append(drawing)
        for t in texts:
            lines.append(f"{n}. {t}")
            n += 1
        lines.append("")
    lines += ["Thanks,", ""]
    return subject, "\n".join(lines)


# --- quantities for the whole tender ----------------------------------------------


def tender_workbook(project: Project) -> bytes:
    """One workbook for the tender: quantities by product (with wastage) and by
    drawing, every room, and what is still to measure by hand."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    drawings = [d for d in project.premeasures.select_related("drawing").prefetch_related("checks")
                if d.summary and not d.busy]
    per_product: dict[str, dict] = {}
    rooms_rows, hand_rows = [], []
    names = []
    for d in drawings:
        floors, skirtings = drawing_mapping(d)
        checks = checks_for(d)
        names.append(d.title)
        for r in d.summary["rooms"]:
            check = checks.get(r["id"])
            product = floors.get(r["finish"] or "", "")
            if check and check.decision == RoomCheck.Decision.BY_HAND:
                hand_rows.append([d.title, r["label"] or "(no room tag)", r["finish"] or "", product,
                                  r["area_m2"], "outline wrong: measure by hand" + (f" ({check.note})" if check.note else "")])
                state = "Measure by hand"
            elif check:
                state = "Checked"
            else:
                state = "Verified" if r["status"] == "Verified" else "To check"
            rooms_rows.append([d.title, r["label"] or "(no room tag)", r["finish"] or "", product or "(no product)",
                               r["area_m2"], state, "; ".join(r["reasons"])])
            if not product or state == "Measure by hand":
                continue
            row = per_product.setdefault(product, {"unit": "m2", "by": defaultdict(float), "check": 0.0})
            row["by"][d.title] += r["area_m2"]
            if state == "To check":
                row["check"] += r["area_m2"]
            for code, length in r["skirting_m"].items():
                sk = skirtings.get(code)
                if sk:
                    srow = per_product.setdefault(sk, {"unit": "m", "by": defaultdict(float), "check": 0.0})
                    srow["by"][d.title] += length
                    if state == "To check":
                        srow["check"] += length
        for code in d.summary.get("manual_finishes") or []:
            hand_rows.append([d.title, "", code, floors.get(code, ""), None, "hatching or linework only"])
        for u in d.summary.get("unknown_fills") or []:
            if u["area_m2"] >= 5:
                hand_rows.append([d.title, "", "", "", u["area_m2"],
                                  f"coloured area not in the legend (RGB {', '.join(map(str, u['colour']))})"])
    for d in project.premeasures.exclude(pk__in=[x.pk for x in drawings]):
        hand_rows.append([d.title, "", "", "", None, f"drawing not measured: {d.get_status_display().lower()}"])

    wastage = wastage_for(project, list(per_product))
    types = dict(LibraryProduct.objects.filter(name__in=list(per_product)).values_list("name", "type"))
    wb = Workbook()
    head = Font(bold=True)
    shade = PatternFill("solid", fgColor="EEF2F6")

    ws = wb.active
    ws.title = "Quantities"
    ws.append([f"{project.code} {project.name}"])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append(["Net quantities from the drawings. Callidus adds cutting waste when rooms are laid out; "
               "the wastage here is the allowance on top."])
    ws.append([])
    cols = ["Product", "Type", "Unit", "Net quantity", "Of which still to check", "Wastage %",
            "Quantity with wastage"] + names
    ws.append(cols)
    for c in ws[4]:
        c.font, c.fill = head, shade
    for product in sorted(per_product):
        row = per_product[product]
        net = sum(row["by"].values())
        pct = wastage.get(product, 0.0)
        ws.append([product, LibraryProduct.Type(types[product]).label if product in types else "", row["unit"],
                   round(net, 2), round(row["check"], 2), pct, round(net * (1 + pct / 100), 2)]
                  + [round(row["by"].get(n, 0), 2) or None for n in names])

    rs = wb.create_sheet("Rooms")
    rs.append(["Drawing", "Room", "Finish", "Product", "Area m2", "State", "Why"])
    for c in rs[1]:
        c.font, c.fill = head, shade
    for row in rooms_rows:
        rs.append(row)

    hs = wb.create_sheet("Measure by hand")
    hs.append(["Drawing", "Room", "Finish", "Product", "Area m2", "Why"])
    for c in hs[1]:
        c.font, c.fill = head, shade
    for row in hand_rows:
        hs.append(row)

    for sheet in (ws, rs, hs):
        for i, width in enumerate((44, 26, 12, 40, 14, 22, 50) + (16,) * len(names), start=1):
            sheet.column_dimensions[get_column_letter(i)].width = width
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def write_ready(project: Project) -> list[Premeasure]:
    """Write every drawing whose finishes all have a product, and every written
    drawing that is out of date. Returns the drawings queued."""
    from .queue import enqueue

    queued = []
    for d in project.premeasures.exclude(summary=None):
        if d.busy:
            continue
        floors, skirtings = drawing_mapping(d)
        stale = written_out_of_date(d, floors, skirtings)
        if not ((d.status == Premeasure.Status.REVIEW and complete(d)) or stale):
            continue
        d.finish_products, d.skirting_products = floors, skirtings
        d.status, d.error = Premeasure.Status.WRITING, ""
        d.save()
        enqueue("jobs.tasks.write_premeasure", d.pk)
        queued.append(d)
    return queued


def zoom_page_path(pm: Premeasure) -> Path:
    from django.conf import settings

    return Path(settings.MEDIA_ROOT) / "premeasure" / "zoom" / f"{pm.pk}.jpg"
