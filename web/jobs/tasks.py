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

from .models import BackgroundTemplate, Premeasure  # noqa: E402
from .setup import BaseJob, base_job, default_mapping, new_products_for  # noqa: E402


def safe_name(text: str) -> str:
    """A file name Windows and Callidus accept."""
    name = re.sub(r'[<>:"/\\|?*&\x00-\x1f]', " ", text)
    return re.sub(r"\s+", " ", name).strip(" .")[:100] or "premeasure"


def _fail(pm: Premeasure, exc: Exception) -> None:
    pm.status = Premeasure.Status.FAILED
    pm.error = f"{type(exc).__name__}: {exc}"
    pm.save(update_fields=["status", "error", "updated_at"])


def _setup_names(pm: Premeasure) -> list[str]:
    return [p.name for p in pm.project.products.all()]


def _analysis(pm: Premeasure):
    from premeasure_engine.premeasure import analyse

    if pm.analysis_file:
        try:
            with pm.analysis_file.open("rb") as f:
                return pickle.load(f)  # written by our own worker, never uploaded
        except Exception:
            pass  # stale after an engine update: read the drawing again
    return analyse(pm.drawing.file.path, _setup_names(pm), page=pm.page - 1)


def _save_file(pm: Premeasure, field: str, path: Path) -> None:
    old = getattr(pm, field)
    if old:
        old.delete(save=False)
    with path.open("rb") as f:
        getattr(pm, field).save(f"{pm.pk}-{path.name}", File(f), save=False)


def analyse_premeasure(pk: int) -> None:
    from premeasure_engine.premeasure import analyse, render_preview, summary

    pm = Premeasure.objects.select_related("drawing", "starter", "project").get(pk=pk)
    pm.status, pm.error = Premeasure.Status.READING, ""
    pm.save(update_fields=["status", "error", "updated_at"])
    try:
        a = analyse(pm.drawing.file.path, _setup_names(pm), page=pm.page - 1)
        pm.summary = summary(a)
        pm.finish_products, pm.skirting_products = default_mapping(pm.project, pm.summary)
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "analysis.pickle"
            work.write_bytes(pickle.dumps(a))
            png = render_preview(a, Path(tmp) / "preview.png", highlight=set(pm.finish_products))
            _save_file(pm, "analysis_file", work)
            _save_file(pm, "preview", png)
        pm.summary["preview_for"] = sorted(pm.finish_products)
        pm.status = Premeasure.Status.REVIEW
        pm.save()
    except Exception as exc:
        _fail(pm, exc)


def refresh_preview(pm: Premeasure, codes: set[str]) -> None:
    """Redraw the preview when the finishes with a product have changed (for example after setup)."""
    from premeasure_engine.premeasure import render_preview

    if not pm.summary or sorted(codes) == pm.summary.get("preview_for"):
        return
    a = _analysis(pm)
    with tempfile.TemporaryDirectory() as tmp:
        _save_file(pm, "preview", render_preview(a, Path(tmp) / "preview.png", highlight=codes))
    pm.summary["preview_for"] = sorted(codes)
    pm.save(update_fields=["preview", "summary", "updated_at"])


def _job(pm: Premeasure) -> BaseJob:
    if pm.starter:
        return BaseJob(Path(pm.starter.file.path), pm.starter)
    job = base_job(pm.project)
    if job is None:
        raise ValueError("No Callidus job to build on. Ask the administrator to add the house Callidus template.")
    return job


def background_template(pm: Premeasure) -> Path:
    """The project job's own .CalBack if it has one sheet, else the house template's."""
    from premeasure_engine.premeasure import calback_sheets

    own = pm.starter.background if pm.starter else None
    for candidate in ([own.file.path] if own else []) + [t.file.path for t in BackgroundTemplate.objects.all()]:
        try:
            if calback_sheets(candidate) == 1:
                return Path(candidate)
        except Exception:
            continue
    raise ValueError(
        "No one-sheet Callidus background to use as a template. Ask the administrator to add the "
        "house Callidus template (any Crean .CalBack with one sheet)."
    )


def write_premeasure(pk: int) -> None:
    from premeasure_engine.premeasure import render_preview, write_outputs

    pm = Premeasure.objects.select_related("drawing", "starter", "project").get(pk=pk)
    try:
        job = _job(pm)
        new, problems = new_products_for(
            pm.project, list(pm.finish_products.values()) + list(pm.skirting_products.values()), job
        )
        if problems:
            raise ValueError("; ".join(problems))
        a = _analysis(pm)
        template = background_template(pm)
        name = safe_name(pm.title)
        with tempfile.TemporaryDirectory() as tmp:
            out = write_outputs(a, job.est, template, pm.finish_products, pm.skirting_products,
                                Path(tmp) / name, name, new_products=new)
            zpath = Path(tmp) / f"{name}.zip"
            with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
                for p in (out.est, out.calback, out.review, out.readme):
                    z.write(p, f"{name}/{p.name}")
            png = render_preview(a, Path(tmp) / "preview.png", highlight=set(pm.finish_products))
            _save_file(pm, "output", zpath)
            _save_file(pm, "preview", png)
        pm.summary["preview_for"] = sorted(pm.finish_products)
        pm.written = {
            "rooms": out.written,
            "area_m2": out.written_m2,
            "left_out_rooms": len(out.left_out),
            "left_out_m2": round(sum(r.area for r in out.left_out), 2),
            "new_products": [p.name for p in new],
            "built_on": job.label,
        }
        pm.status, pm.error = Premeasure.Status.DONE, ""
        pm.save()
    except Exception as exc:
        _fail(pm, exc)
