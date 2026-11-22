"""Project setup: the tender's products, and how drawings use them.

Crean keeps one product list (LibraryProduct). A project's setup links each
finish code on its drawings to a product from that list (Product). A product
is written to Callidus as it is if the project's Callidus job (or the house
template) already has one of that name; otherwise the platform adds it,
copying Callidus's roll settings from a product of the same type.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .models import BackgroundTemplate, LibraryProduct, Product, Project, Upload


def norm(code: str) -> str:
    base, _, rest = code.partition(" (")
    return re.sub(r"[^A-Z0-9]", "", base.upper()) + (f" ({rest}" if rest else "")


@dataclass
class BaseJob:
    est: Path
    upload: Upload | None  # None: the house template

    @property
    def label(self) -> str:
        return self.upload.filename if self.upload else "the house Callidus template"


def house_template() -> BackgroundTemplate | None:
    return BackgroundTemplate.objects.first()


def house_job() -> BaseJob | None:
    house = BackgroundTemplate.objects.exclude(est="").first()
    return BaseJob(Path(house.est.path), None) if house else None


def base_job(project: Project) -> BaseJob | None:
    """The Callidus job new files are built on: the project's latest, else the house template."""
    own = project.uploads.filter(kind=Upload.Kind.CALLIDUS_EST).order_by("-uploaded_at").first()
    if own:
        return BaseJob(Path(own.file.path), own)
    return house_job()


def base_products(job: BaseJob | None):
    from premeasure_engine.callidus.material_writer import existing_products

    if job is None:
        return []
    try:
        return existing_products(job.est)
    except Exception:
        return []


_WORDS = re.compile(r"[A-Z]{4,}")
_PREFER = {
    "sheet": set(),
    "matwell": {"MATWELL", "MATTING", "ENTRANCE"},
    "skirting": {"SKIRTING", "COVED", "COVING"},
    "edge": {"FRAME", "TRIM", "STRIP", "THRESHOLD"},
}


def settings_source(lib: LibraryProduct, existing) -> str:
    """The product in the Callidus job whose settings a new product copies.
    Tiles cannot be added this way yet, so they get none."""
    copyable = {e.name: e for e in existing if e.copyable}
    if lib.callidus_settings_from in copyable:
        return lib.callidus_settings_from
    wanted = {"sheet": "sheet", "matwell": "sheet", "skirting": "edge", "edge": "edge"}.get(lib.type)
    options = [e for e in copyable.values() if e.kind == wanted]
    if not options:
        return ""
    words = set(_WORDS.findall(lib.name.upper())) | _PREFER.get(lib.type, set())
    return max(options, key=lambda e: len(words & set(_WORDS.findall(e.name.upper())))).name


def type_from_callidus(name: str, kind: str) -> str:
    up = name.upper()
    if kind == "tile":
        return LibraryProduct.Type.CARPET_TILE if "CARPET" in up else LibraryProduct.Type.TILE
    if kind == "edge":
        return LibraryProduct.Type.SKIRTING if ("SKIRT" in up or "COV" in up) else LibraryProduct.Type.EDGE
    if "MATWELL" in up or "MATTING" in up:
        return LibraryProduct.Type.MATWELL
    return LibraryProduct.Type.SHEET


def _suggest(finishes: list[dict], library: list[LibraryProduct]) -> dict[str, LibraryProduct]:
    """Confident matches of legend finishes to products in the list."""
    from premeasure_engine.drawing.legend import LegendEntry
    from premeasure_engine.drawing.products import suggest_materials

    out = {}
    for kind in ("floor", "skirting"):
        pool = [p for p in library if p.is_floor == (kind == "floor")]
        mine = [f for f in finishes if f["kind"] == kind]
        entries = [
            LegendEntry(kind, 0, "", f["description"], tuple(f["fill"]) if f.get("fill") else None,
                        custom_code=f["code"] if f.get("custom_code", True) else None)
            for f in mine
        ]
        by_name = {p.name: p for p in pool}
        taken: set[str] = set()
        for f, s in zip(mine, suggest_materials(entries, list(by_name))):  # same order as the entries
            if s.material and s.score >= 2.5 and s.material not in taken:
                out[f["code"]] = by_name[s.material]
                taken.add(s.material)
    return out


def _legend_description(f: dict) -> str:
    return re.sub(r"\s+", " ", f.get("description") or "").strip(" .,-")[:300]


def fill_from_summary(project: Project, summary: dict) -> list[Product]:
    """Add the drawing's legend finishes the setup lacks, with a product from
    the list where it clearly matches and none chosen otherwise."""
    have = {norm(p.finish_code) for p in project.products.all()}
    todo = [f for f in summary["finishes"] if norm(f["code"]) not in have]
    used = set(project.products.exclude(library=None).values_list("library_id", flat=True))
    matches = _suggest(todo, list(LibraryProduct.objects.filter(active=True).exclude(id__in=used)))
    added = []
    for f in todo:
        if norm(f["code"]) in have:
            continue
        added.append(
            Product.objects.create(
                project=project, finish_code=f["code"][:30], library=matches.get(f["code"]),
                description=_legend_description(f), kind=f["kind"], spec_source="Drawing legend",
            )
        )
        have.add(norm(f["code"]))
    return added


def import_from_job(project: Project, upload: Upload, legends: list[dict], user=None) -> tuple[int, int]:
    """Add a Callidus job's products to the product list, and link them to this
    project's finish codes where a drawing's legend clearly matches (codes are
    not taken from product names: a contractor's FF8 need not be the drawing's).
    Returns (products added to the list, finish codes linked)."""
    from premeasure_engine.callidus.material_writer import existing_products

    added, libs = 0, []
    for e in existing_products(upload.file.path):
        if e.name == "NONE":
            continue
        lib, created = LibraryProduct.objects.get_or_create(
            name=e.name[:200],
            defaults={"type": type_from_callidus(e.name, e.kind), "created_by": user,
                      "fill": "#%02x%02x%02x" % e.colour if e.colour else "",
                      "notes": f"From Callidus job {upload.filename}"[:500]},
        )
        added += created
        libs.append(lib)
    rows = {norm(p.finish_code): p for p in project.products.all()}
    finishes = {f["code"]: f for s in legends for f in s["finishes"]}
    open_finishes = [f for code, f in finishes.items() if norm(code) not in rows or rows[norm(code)].library is None]
    linked = 0
    for code, lib in _suggest(open_finishes, libs).items():
        row = rows.get(norm(code))
        if row is None:
            Product.objects.create(project=project, finish_code=code[:30], library=lib,
                                   description=_legend_description(finishes[code]), kind=finishes[code]["kind"],
                                   spec_source=f"Callidus job {upload.filename}"[:200])
        else:
            row.library = lib
            row.save(update_fields=["library"])
        linked += 1
    return added, linked


def default_mapping(project: Project, summary: dict) -> tuple[dict, dict]:
    """Each drawing finish's product, from the setup by finish code."""
    by_code = {norm(p.finish_code): p for p in project.products.select_related("library") if p.library}
    floors, skirtings = {}, {}
    for f in summary["finishes"]:
        p = by_code.get(norm(f["code"]))
        if p is not None and p.library.is_floor == (f["kind"] == "floor"):
            (floors if f["kind"] == "floor" else skirtings)[f["code"]] = p.library.name
    return floors, skirtings


def unmapped(summary: dict, floors: dict, skirtings: dict) -> list[dict]:
    """Finishes found on the drawing (with area or length) that have no product."""
    return [
        f for f in summary["finishes"]
        if f["amount"] > 0 and f["code"] not in (floors if f["kind"] == "floor" else skirtings)
    ]


def new_products_for(chosen: list[str], job: BaseJob):
    """The products a write must add to the job, and any problem with them."""
    from premeasure_engine.callidus.material_writer import NewProduct

    existing = base_products(job)
    names = {e.name for e in existing}
    libs = {p.name: p for p in LibraryProduct.objects.filter(name__in=set(chosen))}
    new, problems = [], []
    for name in dict.fromkeys(chosen):
        if name in names:
            continue
        lib = libs.get(name)
        if lib is None:
            problems.append(f"{name}: not in the product list")
            continue
        like = settings_source(lib, existing)
        if not like:
            what = "tile" if lib.type in ("tile", "carpet_tile") else lib.get_type_display().lower()
            problems.append(
                f"{name}: the platform cannot add a new {what} product to Callidus yet. "
                f"Add it to the Callidus template, or import a Callidus job that has it."
            )
            continue
        new.append(NewProduct(name=lib.name, like=like, colour=lib.rgb))
    return new, problems


def wastage_for(project: Project, names: list[str]) -> dict[str, float]:
    from .models import MeasuringRules

    rules = MeasuringRules.for_project(project)
    out = {}
    for lib in LibraryProduct.objects.filter(name__in=set(names)):
        pct = lib.wastage_pct if lib.wastage_pct is not None else rules.wastage_for(lib.type)
        out[lib.name] = float(pct or 0)
    return out
