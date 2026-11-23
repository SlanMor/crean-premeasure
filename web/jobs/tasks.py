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

from django.conf import settings  # noqa: E402
from django.core.mail import send_mail  # noqa: E402
from django.urls import reverse  # noqa: E402

from .models import BackgroundTemplate, Batch, LibraryProduct, MeasuringRules, Premeasure  # noqa: E402
from .setup import BaseJob, base_job, default_mapping, new_products_for, unmapped, wastage_for  # noqa: E402


def safe_name(text: str) -> str:
    """A file name Windows and Callidus accept."""
    name = re.sub(r'[<>:"/\\|?*&\x00-\x1f]', " ", text)
    return re.sub(r"\s+", " ", name).strip(" .")[:100] or "premeasure"


def _fail(pm: Premeasure, exc: Exception) -> None:
    from premeasure_engine.drawing.pdf_reader import PictureOnly, ScaleNotFound
    from premeasure_engine.drawing.picture import NoLegend, SheetNeeded

    plain = (PictureOnly, ScaleNotFound, SheetNeeded, NoLegend)
    pm.status = Premeasure.Status.FAILED
    pm.error = str(exc) if isinstance(exc, plain) else f"{type(exc).__name__}: {exc}"
    pm.save(update_fields=["status", "error", "updated_at"])


def _setup_names(pm: Premeasure) -> list[str]:
    return list(LibraryProduct.objects.filter(active=True).values_list("name", flat=True))


def _min_opening(pm: Premeasure) -> float:
    return float(MeasuringRules.for_project(pm.project).min_opening_m2)


def _analysis(pm: Premeasure):
    from premeasure_engine.premeasure import analyse

    if pm.analysis_file:
        try:
            with pm.analysis_file.open("rb") as f:
                return pickle.load(f)  # written by our own worker, never uploaded
        except Exception:
            pass  # stale after an engine update: read the drawing again
    return analyse(pm.drawing.file.path, _setup_names(pm), page=pm.page - 1, min_opening=_min_opening(pm),
                   scale=pm.scale, sheet=pm.sheet or None)


def _save_file(pm: Premeasure, field: str, path: Path) -> None:
    old = getattr(pm, field)
    if old:
        old.delete(save=False)
    with path.open("rb") as f:
        getattr(pm, field).save(f"{pm.pk}-{path.name}", File(f), save=False)


def analyse_premeasure(pk: int) -> None:
    from premeasure_engine.premeasure import analyse, render_preview, summary

    pm = Premeasure.objects.select_related("drawing", "starter", "project", "batch").get(pk=pk)
    pm.status, pm.error = Premeasure.Status.READING, ""
    pm.save(update_fields=["status", "error", "updated_at"])
    try:
        a = analyse(pm.drawing.file.path, _setup_names(pm), page=pm.page - 1, min_opening=_min_opening(pm),
                    scale=pm.scale, sheet=pm.sheet or None)
        pm.summary = summary(a)
        pm.summary["min_opening_m2"] = _min_opening(pm)
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
        # rooms are numbered afresh on every reading, so earlier checks no longer apply
        pm.checks.all().delete()
        from .tender import zoom_page_path

        zoom_page_path(pm).unlink(missing_ok=True)
        if pm.supersedes_id:
            from .revisions import compare

            try:
                compare(pm)  # what changed since the last revision; unchanged rooms keep their checks
            except Exception as exc:  # the drawing itself was read: never fail it over the comparison
                pm.revision_diff = {"error": f"{type(exc).__name__}: {exc}"}
                pm.save(update_fields=["revision_diff", "updated_at"])
    except Exception as exc:
        _fail(pm, exc)
        _batch_progress(pm)
        return
    complete = pm.finish_products and not unmapped(pm.summary, pm.finish_products, pm.skirting_products)
    if pm.batch and pm.batch.auto_write and complete:
        pm.status = Premeasure.Status.WRITING
        pm.save(update_fields=["status", "updated_at"])
        write_premeasure(pm.pk)  # same worker job: the batch carries on drawing by drawing
    else:
        _batch_progress(pm)


def split_pdf(upload_id: int, batch_id: int, level: str, scale: int | None = None, sheet: str = "") -> None:
    """A PDF with several pages: one drawing for each page that looks like a finishes plan."""
    from premeasure_engine.premeasure import pdf_pages, plan_pages

    from .models import Upload
    from .queue import enqueue

    from django.db.models import F

    upload = Upload.objects.get(pk=upload_id)
    batch = Batch.objects.get(pk=batch_id)
    try:
        pages = plan_pages(upload.file.path) or list(range(pdf_pages(upload.file.path)))
    except Exception:
        pages = [0]  # unreadable text: try the first page and let the reading report the problem
    from .revisions import prior_revision

    stem = Path(upload.filename).stem
    made = []
    for p in pages:
        prior = prior_revision(batch.project, upload.drawing_number or upload.filename, p + 1,
                               exclude=[m.pk for m in made] + list(upload.premeasures.values_list("pk", flat=True)))
        made.append(Premeasure.objects.create(
            project=batch.project, level=level, title=safe_name(f"{batch.project.code} {stem[:40]} p{p + 1}"),
            drawing=upload, page=p + 1, starter=_starter(batch.project), created_by=batch.created_by,
            batch=batch, status=Premeasure.Status.QUEUED, scale=scale, sheet=sheet,
            supersedes=prior, revision_link="auto" if prior else "",
        ))
    Batch.objects.filter(pk=batch.pk).update(files_pending=F("files_pending") - 1)
    for pm in made:
        enqueue("jobs.tasks.analyse_premeasure", pm.pk)
    _batch_progress_batch(Batch.objects.get(pk=batch.pk))


def _starter(project):
    job = base_job(project)
    return job.upload if job else None


def _batch_progress(pm: Premeasure) -> None:
    if pm.batch_id:
        _batch_progress_batch(Batch.objects.get(pk=pm.batch_id))


def _batch_progress_batch(batch: Batch) -> None:
    """Email the person who started the batch once every drawing in it has finished."""
    from django.utils import timezone

    batch.refresh_from_db()
    drawings = list(batch.premeasures.all())
    if batch.notified_at or batch.files_pending or not drawings or any(d.busy for d in drawings):
        return
    batch.notified_at = timezone.now()
    batch.save(update_fields=["notified_at"])
    user = batch.created_by
    if not (batch.notify and user and user.email and settings.EMAIL_HOST):
        return
    lines = [f"{d.title}: {d.get_status_display()}" for d in drawings]
    link = settings.SITE_URL.rstrip("/") + reverse("batch", args=[batch.pk])
    done = sum(d.status == Premeasure.Status.DONE for d in drawings)
    try:
        send_mail(
            f"{batch.project.code}: {len(drawings)} drawings read, {done} ready in Callidus",
            "\n".join([f"Hi {user.first_name or user.username},", "",
                        f"The drawings you uploaded for {batch.project} have been read.", "", *lines, "",
                        f"See them here: {link}", "", "Crean pre-measure"]),
            None, [user.email],
        )
    except Exception:
        pass  # the batch page shows the same; a mail failure must not fail the drawing


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
    from django.utils import timezone
    from premeasure_engine.premeasure import render_preview, write_outputs

    pm = Premeasure.objects.select_related("drawing", "starter", "project").get(pk=pk)
    try:
        job = _job(pm)
        chosen = list(pm.finish_products.values()) + list(pm.skirting_products.values())
        new, problems = new_products_for(chosen, job)
        if problems:
            raise ValueError("; ".join(problems))
        from premeasure_engine.premeasure import apply_checks

        a, by_hand = apply_checks(_analysis(pm), {c.room_id: c.decision for c in pm.checks.all()})
        template = background_template(pm)
        name = safe_name(pm.title)
        with tempfile.TemporaryDirectory() as tmp:
            out = write_outputs(a, job.est, template, pm.finish_products, pm.skirting_products,
                                Path(tmp) / name, name, new_products=new,
                                wastage=wastage_for(pm.project, chosen))
            zpath = Path(tmp) / f"{name}.zip"
            with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
                for p in (out.est, out.calback, out.review, out.quantities, out.readme):
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
            "by_hand_rooms": len(by_hand),
            "by_hand_m2": round(sum(r.area for r in by_hand), 2),
            "at": timezone.now().isoformat(),
        }
        pm.status, pm.error = Premeasure.Status.DONE, ""
        pm.save()
    except Exception as exc:
        _fail(pm, exc)
    _batch_progress(pm)
