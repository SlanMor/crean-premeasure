"""Project setup: the tender's products, and how drawings use them.

A project's products are set once (Project setup). Each drawing's legend
finishes are matched to them by finish code. A product is written to
Callidus as it is if the project's Callidus job (or the house template)
already has one of that name; otherwise the platform adds it, copying the
settings of ``settings_from``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .models import BackgroundTemplate, Product, Project, Upload


def norm(code: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", code.split(" (")[0].upper()) + (
        code[code.index(" (") :] if " (" in code else ""
    )


@dataclass
class BaseJob:
    est: Path
    upload: Upload | None  # None: the house template

    @property
    def label(self) -> str:
        return self.upload.filename if self.upload else "the house Callidus template"


def base_job(project: Project) -> BaseJob | None:
    """The Callidus job new files are built on: the project's latest, else the house template."""
    own = project.uploads.filter(kind=Upload.Kind.CALLIDUS_EST).order_by("-uploaded_at").first()
    if own:
        return BaseJob(Path(own.file.path), own)
    house = BackgroundTemplate.objects.exclude(est="").first()
    if house:
        return BaseJob(Path(house.est.path), None)
    return None


def base_products(job: BaseJob | None):
    from premeasure_engine.callidus.material_writer import existing_products

    if job is None:
        return []
    try:
        return existing_products(job.est)
    except Exception:
        return []


def guess_settings(name: str, kind: str, existing) -> str:
    """The product in the job a new product most likely shares settings with."""
    wanted = "edge" if kind == Product.Kind.SKIRTING else "sheet"
    options = [e for e in existing if e.copyable and e.kind == wanted]
    if not options:
        return ""
    words = set(re.findall(r"[A-Z]{4,}", name.upper()))
    if kind == Product.Kind.SKIRTING:
        words |= {"SKIRTING", "COVED"}
    best = max(options, key=lambda e: len(words & set(re.findall(r"[A-Z]{4,}", e.name.upper()))))
    return best.name


def _tidy(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip(" .,-")
    return text[:150]


def fill_from_summary(project: Project, summary: dict) -> list[Product]:
    """Add the drawing's legend finishes that the project does not have yet."""
    have = {norm(p.finish_code) for p in project.products.exclude(finish_code="")}
    existing = base_products(base_job(project))
    added = []
    for f in summary["finishes"]:
        code = f["code"]
        if norm(code) in have:
            continue
        kind = Product.Kind.FLOOR if f["kind"] == "floor" else Product.Kind.SKIRTING
        name = f"{code.split(' (')[0]} - {_tidy(f['description'])}" if f["description"] else code
        fill = "#%02x%02x%02x" % tuple(f["fill"]) if f["fill"] else ""
        added.append(
            Product.objects.create(
                project=project, finish_code=code[:30], kind=kind, name=name[:200], fill=fill,
                settings_from=guess_settings(name, kind, existing), spec_source="Drawing legend",
            )
        )
        have.add(norm(code))
    return added


def import_from_job(project: Project, upload: Upload, legends: list[dict]) -> list[Product]:
    """Add a Callidus job's products to the setup. A finish code is only filled
    in when a drawing's legend clearly matches the product (codes are not
    taken from product names: a contractor's FF8 need not be the drawing's)."""
    from premeasure_engine.callidus.material_writer import existing_products
    from premeasure_engine.drawing.legend import LegendEntry
    from premeasure_engine.drawing.products import suggest_materials

    names = {p.name for p in project.products.all()}
    used = {norm(p.finish_code) for p in project.products.exclude(finish_code="")}
    products = [e for e in existing_products(upload.file.path) if e.name != "NONE" and e.name not in names]
    codes: dict[str, str] = {}
    entries = [
        LegendEntry(f["kind"], 0, "", f["description"], tuple(f["fill"]) if f["fill"] else None,
                    custom_code=f["code"])
        for s in legends for f in s["finishes"]
    ]
    for kind in ("floor", "skirting"):
        for s in suggest_materials([e for e in entries if e.kind == kind], [p.name for p in products]):
            if s.material and s.score >= 2.5 and norm(s.legend_code) not in used and s.material not in codes:
                codes[s.material] = s.legend_code
                used.add(norm(s.legend_code))
    added = []
    for e in products:
        added.append(
            Product.objects.create(
                project=project, finish_code=codes.get(e.name, "")[:30],
                kind=Product.Kind.SKIRTING if e.kind == "edge" else Product.Kind.FLOOR,
                name=e.name[:200], spec_source=f"Callidus job {upload.filename}"[:200],
            )
        )
    return added


def default_mapping(project: Project, summary: dict) -> tuple[dict, dict]:
    """Each drawing finish's product: the setup product with its code, else a
    clear suggestion among setup products that have no code yet."""
    products = list(project.products.all())
    by_code = {norm(p.finish_code): p for p in products if p.finish_code}
    free = {p.name for p in products if not p.finish_code}
    floors, skirtings = {}, {}
    for f in summary["finishes"]:
        target = floors if f["kind"] == "floor" else skirtings
        p = by_code.get(norm(f["code"]))
        if p is not None:
            target[f["code"]] = p.name
        elif f["kind"] == "floor" and f.get("suggestion") in free:
            target[f["code"]] = f["suggestion"]
    return floors, skirtings


def new_products_for(project: Project, chosen: list[str], job: BaseJob):
    """The products a write must add to the job, and any problem with them."""
    from premeasure_engine.callidus.material_writer import NewProduct

    existing = {e.name: e for e in base_products(job)}
    setup = {p.name: p for p in project.products.all()}
    new, problems = [], []
    for name in dict.fromkeys(chosen):
        if name in existing:
            continue
        p = setup.get(name)
        if p is None:
            problems.append(f"{name}: not in the project setup")
            continue
        like = p.settings_from or guess_settings(p.name, p.kind, existing.values())
        if like not in existing or not existing[like].copyable:
            problems.append(f"{name}: choose which product's settings it copies in Project setup")
            continue
        new.append(NewProduct(name=p.name, like=like, colour=p.rgb))
    return new, problems
