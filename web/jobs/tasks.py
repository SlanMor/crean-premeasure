"""Background tasks, run by the rq worker."""

from django.utils import timezone

from premeasure_engine.callidus import read_est
from premeasure_engine.quantities import quantities

from .models import Job


def read_callidus_est(job_id: int) -> None:
    job = Job.objects.select_related("upload").get(pk=job_id)
    job.status = Job.Status.RUNNING
    job.save(update_fields=["status"])
    try:
        est = read_est(job.upload.file.path)
        rows = quantities(est)
        job.result = {
            "title": est.title,
            "materials": len(est.materials),
            "rooms": [
                {
                    "room": r.room,
                    "material": r.material,
                    "net_m2": round(r.net_m2, 3),
                    "edge_products_lm": {k: round(v, 3) for k, v in r.edge_products_lm.items()},
                }
                for r in rows
            ],
            "total_m2_excluding_none": round(sum(r.net_m2 for r in rows if r.material != "NONE"), 3),
        }
        job.status = Job.Status.DONE
    except Exception as exc:  # recorded on the job for the estimator to see
        job.status = Job.Status.FAILED
        job.error = f"{type(exc).__name__}: {exc}"
    job.finished_at = timezone.now()
    job.save()


# --- pre-measure: read a drawing, then write the Callidus job -------------

import pickle  # noqa: E402
import re  # noqa: E402
import tempfile  # noqa: E402
import zipfile  # noqa: E402
from pathlib import Path  # noqa: E402

from django.core.files import File  # noqa: E402

from .models import BackgroundTemplate, Premeasure, Product  # noqa: E402


def safe_name(text: str) -> str:
    """A file name Windows and Callidus accept."""
    name = re.sub(r'[<>:"/\\|?*&\x00-\x1f]', " ", text)
    return re.sub(r"\s+", " ", name).strip(" .")[:100] or "premeasure"


def _fail(pm: Premeasure, exc: Exception) -> None:
    pm.status = Premeasure.Status.FAILED
    pm.error = f"{type(exc).__name__}: {exc}"
    pm.save(update_fields=["status", "error", "updated_at"])


def _analysis(pm: Premeasure):
    from premeasure_engine.premeasure import analyse

    if pm.analysis_file:
        try:
            with pm.analysis_file.open("rb") as f:
                return pickle.load(f)  # written by our own worker, never uploaded
        except Exception:
            pass  # stale after an engine update: read the drawing again
    return analyse(pm.drawing.file.path, pm.starter.file.path, page=pm.page - 1)


def _default_products(pm: Premeasure, summary: dict) -> tuple[dict, dict]:
    """Products saved for this project first, then confident suggestions."""
    saved = {p.finish_code: p.callidus_material for p in pm.project.products.all()}
    materials = set(summary["materials"])
    floors, skirtings = {}, {}
    for f in summary["finishes"]:
        target = floors if f["kind"] == "floor" else skirtings
        if saved.get(f["code"]) in materials:
            target[f["code"]] = saved[f["code"]]
        elif saved.get(f["code"]) == "":
            continue  # left out on an earlier drawing of this project
        elif f["kind"] == "floor" and f["suggestion"]:
            target[f["code"]] = f["suggestion"]
    return floors, skirtings


def analyse_premeasure(pk: int) -> None:
    from premeasure_engine.premeasure import render_preview, summary

    pm = Premeasure.objects.select_related("drawing", "starter", "project").get(pk=pk)
    pm.status, pm.error = Premeasure.Status.READING, ""
    pm.save(update_fields=["status", "error", "updated_at"])
    try:
        from premeasure_engine.premeasure import analyse

        a = analyse(pm.drawing.file.path, pm.starter.file.path, page=pm.page - 1)
        pm.summary = summary(a)
        pm.finish_products, pm.skirting_products = _default_products(pm, pm.summary)
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "analysis.pickle"
            work.write_bytes(pickle.dumps(a))
            png = render_preview(a, Path(tmp) / "preview.png", highlight=set(pm.finish_products))
            for field, path in (("analysis_file", work), ("preview", png)):
                old = getattr(pm, field)
                if old:
                    old.delete(save=False)
                with path.open("rb") as f:
                    getattr(pm, field).save(f"{pm.pk}-{path.name}", File(f), save=False)
        pm.status = Premeasure.Status.REVIEW
        pm.save()
    except Exception as exc:
        _fail(pm, exc)


def background_template(pm: Premeasure) -> Path:
    """The starter's own .CalBack if it has one sheet, else the house template."""
    from premeasure_engine.premeasure import calback_sheets

    own = pm.starter.background
    for candidate in ([own.file.path] if own else []) + [t.file.path for t in BackgroundTemplate.objects.all()]:
        try:
            if calback_sheets(candidate) == 1:
                return Path(candidate)
        except Exception:
            continue
    raise ValueError(
        "No one-sheet Callidus background to use as a template. Ask the administrator to add one "
        "under Background templates (any Crean .CalBack with one sheet)."
    )


def write_premeasure(pk: int) -> None:
    from premeasure_engine.premeasure import render_preview, write_outputs

    pm = Premeasure.objects.select_related("drawing", "starter", "project").get(pk=pk)
    try:
        a = _analysis(pm)
        template = background_template(pm)
        name = safe_name(pm.title)
        with tempfile.TemporaryDirectory() as tmp:
            out = write_outputs(a, pm.starter.file.path, template, pm.finish_products,
                                pm.skirting_products, Path(tmp) / name, name)
            zpath = Path(tmp) / f"{name}.zip"
            with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
                for p in (out.est, out.calback, out.review, out.readme):
                    z.write(p, f"{name}/{p.name}")
            png = render_preview(a, Path(tmp) / "preview.png", highlight=set(pm.finish_products))
            for field, path in (("output", zpath), ("preview", png)):
                old = getattr(pm, field)
                if old:
                    old.delete(save=False)
                with path.open("rb") as f:
                    getattr(pm, field).save(f"{pm.pk}-{path.name}", File(f), save=False)
        pm.written = {
            "rooms": out.written,
            "area_m2": out.written_m2,
            "left_out_rooms": len(out.left_out),
            "left_out_m2": round(sum(r.area for r in out.left_out), 2),
        }
        pm.status, pm.error = Premeasure.Status.DONE, ""
        pm.save()
        _remember_products(pm)
    except Exception as exc:
        _fail(pm, exc)


def _remember_products(pm: Premeasure) -> None:
    """Keep the confirmed products on the project, so the next drawing starts from them."""
    desc = {f["code"]: f["description"] for f in pm.summary["finishes"]}
    suggested = {f["code"] for f in pm.summary["finishes"] if f.get("suggestion")}
    chosen = {**pm.finish_products, **pm.skirting_products}
    for code in desc:
        material = chosen.get(code, "")  # "" records that a suggested finish was left out
        if not material and code not in suggested and not pm.project.products.filter(finish_code=code[:30]).exists():
            continue
        Product.objects.update_or_create(
            project=pm.project, finish_code=code[:30],
            defaults={"callidus_material": material[:200], "name": (desc.get(code) or material or code)[:200],
                      "spec_source": f"Confirmed on {pm.title}"[:200]},
        )


