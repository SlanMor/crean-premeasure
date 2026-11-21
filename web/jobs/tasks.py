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
