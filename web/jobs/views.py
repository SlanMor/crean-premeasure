from pathlib import Path

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import connection
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .forms import PremeasureForm, ProductsForm, ProjectForm, SetupFormSet, StarterForm
from .models import Premeasure, Project, Upload
from .queue import enqueue
from .setup import base_job, base_products, default_mapping, fill_from_summary, import_from_job
from .tasks import refresh_preview, safe_name


def health(request):
    """Used by deploy checks: confirms the app and database respond."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return JsonResponse({"status": "ok"})


@login_required
def home(request):
    projects = Project.objects.all()
    form = ProjectForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        project = form.save()
        return redirect("setup", pk=project.pk)
    recent = Premeasure.objects.select_related("project").all()[:10]
    return render(request, "jobs/home.html", {"projects": projects, "form": form, "recent": recent})


@login_required
def project(request, pk: int):
    project = get_object_or_404(Project, pk=pk)
    return render(
        request,
        "jobs/project.html",
        {
            "project": project,
            "form": PremeasureForm(),
            "premeasures": project.premeasures.all(),
            "products": project.products.all(),
            "job": base_job(project),
        },
    )


@login_required
def setup(request, pk: int):
    """Project setup: the products this tender uses."""
    project = get_object_or_404(Project, pk=pk)
    job = base_job(project)
    existing = base_products(job)
    names = {e.name for e in existing}
    choices = [e.name for e in existing if e.copyable]
    kwargs = {"settings_choices": choices, "existing": names}
    qs = project.products.all()
    formset = SetupFormSet(request.POST or None, queryset=qs, form_kwargs=kwargs, prefix="p")
    if request.method == "POST" and formset.is_valid():
        changed = formset.save(commit=False)
        for obj in formset.deleted_objects:
            obj.delete()
        for obj in changed:
            obj.project = project
            if not obj.spec_source:
                obj.spec_source = "Entered in project setup"
            obj.save()
        messages.success(request, "Project setup saved.")
        return _back(request, request.GET.get("next"), reverse("setup", args=[pk]))
    drawings = project.premeasures.exclude(summary=None)
    return render(
        request,
        "jobs/setup.html",
        {"project": project, "formset": formset, "job": job, "drawings": drawings, "starter_form": StarterForm(),
         "next": request.GET.get("next", "")},
    )


@login_required
@require_POST
def fill_setup(request, pk: int, pm: int):
    """Add a drawing's legend finishes to the project setup."""
    project = get_object_or_404(Project, pk=pk)
    drawing = get_object_or_404(Premeasure, pk=pm, project=project)
    added = fill_from_summary(project, drawing.summary or {"finishes": []})
    messages.success(
        request,
        f"Added {len(added)} finish{'es' if len(added) != 1 else ''} from {drawing.title}. "
        "Check each product name and which product its settings copy."
        if added else "Every finish on that drawing is already in the setup.",
    )
    url = reverse("setup", args=[pk])
    nxt = request.POST.get("next", "")
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        url += f"?next={nxt}"
    return redirect(url)


@login_required
@require_POST
def add_starter(request, pk: int):
    project = get_object_or_404(Project, pk=pk)
    form = StarterForm(request.POST, request.FILES)
    if form.is_valid():
        est = Upload.objects.create(project=project, kind=Upload.Kind.CALLIDUS_EST, file=form.cleaned_data["est"])
        if form.cleaned_data.get("calback"):
            est.background = Upload.objects.create(
                project=project, kind=Upload.Kind.CALLIDUS_BACKGROUND, file=form.cleaned_data["calback"]
            )
            est.save(update_fields=["background"])
        legends = [d.summary for d in project.premeasures.exclude(summary=None)]
        added = import_from_job(project, est, legends)
        messages.success(request, f"Added {est.filename}: {len(added)} products added to the setup.")
    else:
        for errors in form.errors.values():
            for e in errors:
                messages.error(request, e)
    return redirect("setup", pk=pk)


@login_required
def new_premeasure(request, pk: int):
    project = get_object_or_404(Project, pk=pk)
    form = PremeasureForm(request.POST or None, request.FILES or None)
    job = base_job(project)
    if job is None:
        messages.error(request, "There is no Callidus job to build on yet. Ask the administrator to add the "
                       "house Callidus template, or add one for this project in Project setup.")
        return redirect("project", pk=pk)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        drawing = Upload.objects.create(
            project=project, kind=Upload.Kind.DRAWING, file=d["drawing"],
            drawing_number=Path(d["drawing"].name).stem[:100], revision=d["revision"],
        )
        title = d["title"] or " ".join(x for x in (project.code, d["level"] or Path(d["drawing"].name).stem[:40]) if x)
        pm = Premeasure.objects.create(
            project=project, level=d["level"], title=safe_name(title), drawing=drawing, page=d["page"],
            starter=job.upload, created_by=request.user,
        )
        enqueue("jobs.tasks.analyse_premeasure", pm.pk)
        return redirect("premeasure", pk=pm.pk)
    return render(request, "jobs/new_premeasure.html", {"project": project, "form": form})


def _mapping(pm: Premeasure) -> tuple[dict, dict]:
    """Before the first write, follow the project setup as it is now; after it, the choices made."""
    if pm.status == Premeasure.Status.REVIEW or (pm.status == Premeasure.Status.FAILED and not pm.output):
        return default_mapping(pm.project, pm.summary)
    return pm.finish_products, pm.skirting_products


def _back(request, nxt: str | None, default: str):
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        return redirect(nxt)
    return redirect(default)


def _rooms_view(pm: Premeasure, floors: dict) -> list[dict]:
    rooms = []
    for r in pm.summary["rooms"]:
        product = floors.get(r["finish"] or "", "")
        skirting = ", ".join(f"{k} {v:.2f} m" for k, v in sorted(r["skirting_m"].items()))
        rooms.append({**r, "product": product, "written": bool(product), "skirting": skirting})
    return rooms


@login_required
def premeasure(request, pk: int):
    pm = get_object_or_404(Premeasure.objects.select_related("project", "drawing", "starter"), pk=pk)
    ctx = {"pm": pm, "project": pm.project}
    if pm.summary and not pm.busy:
        floors, skirtings = _mapping(pm)
        try:
            refresh_preview(pm, set(floors))
        except Exception:
            pass  # the picture is a convenience; never block the page on it
        setup = {p.name: p for p in pm.project.products.all()}
        form = ProductsForm(summary=pm.summary, floors=floors, skirtings=skirtings, materials=list(setup))
        missing = [f for f in pm.summary["finishes"] if (floors if f["kind"] == "floor" else skirtings).get(f["code"]) is None]
        rooms = _rooms_view(pm, floors)
        ctx.update(
            form=form,
            rooms=rooms,
            n_rooms=len(rooms),
            n_review=sum(r["status"] != "Verified" for r in rooms),
            area=round(sum(r["area_m2"] for r in rooms), 2),
            area_written=round(sum(r["area_m2"] for r in rooms if r["written"]), 2),
            n_written=sum(r["written"] for r in rooms),
            n_setup=len(setup),
            missing=missing,
            in_setup={f["code"] for f in pm.summary["finishes"]} - {f["code"] for f in missing},
        )
    return render(request, "jobs/premeasure.html", ctx)


@login_required
@require_POST
def write(request, pk: int):
    pm = get_object_or_404(Premeasure, pk=pk)
    if pm.busy or not pm.summary:
        return redirect("premeasure", pk=pk)
    names = list(pm.project.products.values_list("name", flat=True))
    form = ProductsForm(request.POST, summary=pm.summary, floors={}, skirtings={}, materials=names)
    if not form.is_valid():
        messages.error(request, "One of the products is no longer in the project setup. Please choose again.")
        return redirect("premeasure", pk=pk)
    pm.finish_products, pm.skirting_products = form.chosen()
    if not pm.finish_products:
        pm.save(update_fields=["finish_products", "skirting_products", "updated_at"])
        messages.error(request, "Choose a product for at least one finish.")
        return redirect("premeasure", pk=pk)
    pm.status, pm.error = Premeasure.Status.WRITING, ""
    pm.save()
    enqueue("jobs.tasks.write_premeasure", pm.pk)
    return redirect("premeasure", pk=pk)


@login_required
@require_POST
def retry(request, pk: int):
    pm = get_object_or_404(Premeasure, pk=pk)
    if pm.status == Premeasure.Status.FAILED:
        if pm.summary:
            pm.status = Premeasure.Status.WRITING
            pm.save(update_fields=["status", "updated_at"])
            enqueue("jobs.tasks.write_premeasure", pm.pk)
        else:
            enqueue("jobs.tasks.analyse_premeasure", pm.pk)
    return redirect("premeasure", pk=pk)


@login_required
def status(request, pk: int):
    pm = get_object_or_404(Premeasure, pk=pk)
    return JsonResponse({"status": pm.status, "busy": pm.busy})


def _serve(field, filename: str | None = None, attachment: bool = False):
    if not field:
        raise Http404
    try:
        f = field.open("rb")
    except FileNotFoundError as exc:
        raise Http404 from exc
    return FileResponse(f, as_attachment=attachment, filename=filename or Path(field.name).name)


@login_required
def preview(request, pk: int):
    pm = get_object_or_404(Premeasure, pk=pk)
    return _serve(pm.preview)


@login_required
def download(request, pk: int):
    pm = get_object_or_404(Premeasure, pk=pk)
    return _serve(pm.output, f"{safe_name(pm.title)}.zip", attachment=True)


@login_required
def drawing(request, pk: int):
    pm = get_object_or_404(Premeasure.objects.select_related("drawing"), pk=pk)
    return _serve(pm.drawing.file)
