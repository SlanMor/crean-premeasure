"""The checked Callidus job back from the estimator: stored against its
drawing, compared with the pre-measure, and turned into Crean's quantities."""

from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path

from .models import CheckedJob, LibraryProduct, Premeasure, Project, Upload

# Crean's summary headings by type of product
KIND_HEADINGS = {
    "sheet": "Vinyl and sheet flooring",
    "tile": "Floor tiling",
    "carpet_tile": "Carpet tiles",
    "matwell": "Entrance matting",
    "skirting": "Coved skirting",
    "edge": "Trims and threshold strips",
}


def _stem(name: str) -> str:
    from .tasks import safe_name

    return safe_name(Path(name).stem).lower()


def match_drawing(project: Project, est_title: str, filename: str) -> Premeasure | None:
    """The drawing a checked job came from: its Callidus files were named after
    the drawing, and the estimator usually keeps the name."""
    from .tasks import safe_name

    keys = {_stem(filename), safe_name(est_title).lower()}
    for pm in project.premeasures.exclude(output=""):
        if safe_name(pm.title).lower() in keys:
            return pm
    return None


def written_est(pm: Premeasure, folder: Path):
    """The .est the platform wrote for a drawing, read back from its zip."""
    from premeasure_engine.callidus import read_est

    if not pm.output:
        return None
    try:
        with pm.output.open("rb") as f, zipfile.ZipFile(f) as z:
            name = next((n for n in z.namelist() if n.lower().endswith(".est")), None)
            if name is None:
                return None
            path = folder / Path(name).name
            path.write_bytes(z.read(name))
    except (FileNotFoundError, zipfile.BadZipFile):
        return None
    return read_est(path)


def add_checked(project: Project, uploaded, user=None, premeasure: Premeasure | None = None) -> CheckedJob:
    """Store a checked Callidus job, match it to its drawing, and compare."""
    from premeasure_engine.callidus import read_est
    from premeasure_engine.roundtrip import compare_jobs, crean_rows

    upload = Upload.objects.create(project=project, kind=Upload.Kind.CALLIDUS_EST, file=uploaded)
    est = read_est(upload.file.path)
    pm = premeasure or match_drawing(project, est.title, uploaded.name)
    rows = crean_rows(est)
    job = CheckedJob(
        project=project, premeasure=pm, upload=upload, title=(est.title or Path(uploaded.name).stem)[:200],
        rooms=sum(1 for r in rows if r.net_m2 is not None), area_m2=round(sum(r.net_m2 or 0 for r in rows), 2),
        uploaded_by=user,
    )
    if pm is not None:
        with tempfile.TemporaryDirectory() as tmp:
            ours = written_est(pm, Path(tmp))
            if ours is not None:
                job.comparison = compare_jobs(ours, est).as_dict()
        CheckedJob.objects.filter(project=project, premeasure=pm, current=True).update(current=False)
    else:
        CheckedJob.objects.filter(project=project, premeasure=None, title=job.title, current=True).update(current=False)
    job.save()
    return job


def current_jobs(project: Project) -> list[CheckedJob]:
    return list(project.checked_jobs.filter(current=True).select_related("premeasure", "upload"))


def crean_quantities(project: Project) -> bytes:
    """Crean's quantities spreadsheet from the tender's checked Callidus jobs."""
    from premeasure_engine.callidus import read_est
    from premeasure_engine.roundtrip import crean_workbook

    jobs = current_jobs(project)
    ests = [read_est(j.upload.file.path) for j in sorted(jobs, key=lambda j: j.title)]
    names = {m.name.strip() for e in ests for m in e.materials}
    kinds = {p.name.strip(): KIND_HEADINGS.get(p.type, "Other")
             for p in LibraryProduct.objects.filter(name__in=names)}
    return crean_workbook(ests, kinds=kinds)


def accuracy(jobs: list[CheckedJob]) -> dict:
    """Totals over checked jobs that have a pre-measure to compare with."""
    compared = [j for j in jobs if j.comparison]
    final = sum(j.comparison["area_final"] for j in compared)
    right = sum(j.comparison["area_final"] * j.comparison["right_first_time"] for j in compared)
    both = sum(j.comparison["area_final"] * j.comparison.get("outline_agreement", 0) for j in compared)
    ours = sum(j.comparison["area_ours"] for j in compared)
    changes = [j.comparison["median_change"] for j in compared if j.comparison["median_change"] is not None]
    counts: dict[str, int] = {}
    for j in compared:
        for k, v in j.comparison["counts"].items():
            counts[k] = counts.get(k, 0) + v
    rft = right / final if final else None
    mid = sorted(changes)[len(changes) // 2] if changes else None
    return {
        "rft_pct": None if rft is None else round(rft * 100),
        "agree_pct": round(both / final * 100, 1) if final else None,
        "extra_pct": round(max(0.0, ours - both) / final * 100, 1) if final else None,
        "change_pct": None if mid is None else round(mid * 100, 1),
        "jobs": len(compared),
        "area_final": round(final, 1),
        "right_first_time": right / final if final else None,
        "median_change": sorted(changes)[len(changes) // 2] if changes else None,
        "counts": {k.replace(" ", "_"): v for k, v in counts.items()},
    }


def not_back(project: Project) -> list[Premeasure]:
    """Drawings written to Callidus with no checked job uploaded yet."""
    back = set(project.checked_jobs.exclude(premeasure=None).values_list("premeasure_id", flat=True))
    return [d for d in project.current_drawings().filter(status=Premeasure.Status.DONE) if d.pk not in back]
