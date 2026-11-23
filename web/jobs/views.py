from pathlib import Path

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.db import connection
from django.db.models import Count, Q
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .forms import (
    LibraryProductForm,
    ProductsForm,
    RulesForm,
    ScaleForm,
    SetupFormSet,
    ShortlistForm,
    StarterForm,
    TemplateForm,
    UploadDrawingsForm,
    UserForm,
)
from .models import Batch, LibraryProduct, MeasuringRules, Premeasure, Product, Project, Upload
from .queue import enqueue
from .setup import (
    base_job,
    base_products,
    chosen_differently,
    keep_in_project,
    drawing_mapping,
    fill_from_summary,
    house_job,
    house_template,
    import_from_job,
    norm,
    unmapped,
)
from .tasks import refresh_preview, safe_name

staff_required = user_passes_test(lambda u: u.is_active and u.is_staff)


def health(request):
    """Used by deploy checks: confirms the app and database respond."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return JsonResponse({"status": "ok"})


def _back(request, nxt: str | None, default: str):
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        return redirect(nxt)
    return redirect(default)


# --- projects ---------------------------------------------------------------


@login_required
def home(request):
    projects = Project.objects.select_related("client", "main_contractor").annotate(n_drawings=Count("premeasures"))
    recent = Premeasure.objects.select_related("project").all()[:10]
    return render(request, "jobs/home.html", {"projects": projects, "recent": recent})


@login_required
def project(request, pk: int):
    from .tender import rooms_to_check, tender_todo

    project = get_object_or_404(Project.objects.select_related("client", "main_contractor", "architect"), pk=pk)
    rows = list(project.products.select_related("library"))
    drawings = list(project.premeasures.prefetch_related("checks"))
    todo = tender_todo(project, request.user)
    return render(
        request,
        "jobs/project.html",
        {
            "project": project,
            "form": UploadDrawingsForm(),
            "premeasures": [(d, len(rooms_to_check(d)) if d.summary and not d.busy else 0) for d in drawings],
            "rows": rows,
            "n_chosen": sum(1 for r in rows if r.library_id),
            "job": base_job(project),
            "own_rules": MeasuringRules.objects.filter(project=project).exists(),
            "todo": [t for t in todo if t.level != "info"],
            "more": [t for t in todo if t.level == "info"],
            "busy": any(d.busy for d in drawings),
        },
    )


@login_required
def setup(request, pk: int):
    """Project setup: which product each finish code on the drawings uses, and
    the products this tender uses (shown first wherever a product is chosen)."""
    project = get_object_or_404(Project, pk=pk)
    mine = set(project.project_products().values_list("id", flat=True))
    formset = SetupFormSet(request.POST or None, queryset=project.products.select_related("library"), prefix="p",
                           form_kwargs={"mine": mine})
    shortlist = ShortlistForm(request.POST or None, prefix="s")
    if request.method == "POST" and formset.is_valid() and shortlist.is_valid():
        changed = formset.save(commit=False)
        for obj in formset.deleted_objects:
            obj.delete()
        for obj in changed:
            obj.project = project
            if not obj.spec_source:
                obj.spec_source = "Entered in project setup"
            obj.save()
        project.shortlist.add(*shortlist.cleaned_data["add"])
        drop = [int(x) for x in request.POST.getlist("drop") if x.isdigit()]
        if drop:
            project.shortlist.remove(*drop)
        messages.success(request, "Products saved.")
        here = request.get_full_path()
        new_for = request.POST.get("new_for", "")
        if new_for:  # saved first, so nothing chosen above is lost
            return redirect(_new_product_url(project, new_for, here))
        if request.POST.get("stay"):
            return redirect(here)
        return _back(request, request.GET.get("next"), here)
    return render(
        request,
        "jobs/setup.html",
        {
            "project": project,
            "formset": formset,
            "shortlist_form": shortlist,
            "shortlist": project.shortlist.filter(active=True),
            "job": base_job(project),
            "drawings": project.premeasures.exclude(summary=None),
            "starter_form": StarterForm(),
            "next": request.GET.get("next", ""),
        },
    )


def _new_product_url(project: Project, new_for: str, back: str) -> str:
    """The new product page, started from what a setup row's legend says."""
    from urllib.parse import urlencode

    params = {"next": back}
    row = project.products.filter(pk=int(new_for)).first() if new_for.isdigit() else None
    if row is not None:
        text = row.description.upper()
        if row.kind == "skirting":
            kind = "skirting"
        elif "CARPET" in text and ("TILE" in text or "PLANK" in text):
            kind = "carpet_tile"
        elif any(w in text for w in ("PORCELAIN", "CERAMIC", "TERRAZZO TILE", "FLOOR TILE")):
            kind = "tile"
        elif "MATWELL" in text or "ENTRANCE MAT" in text:
            kind = "matwell"
        else:
            kind = "sheet"
        name = f"{row.finish_code} - {row.description}"
        params.update(row=row.pk, type=kind, name=name[:97] + "..." if len(name) > 100 else name)
    return f"{reverse('product_new')}?{urlencode(params)}"


@login_required
@require_POST
def fill_setup(request, pk: int, pm: int):
    """Add a drawing's legend finishes to the project setup."""
    project = get_object_or_404(Project, pk=pk)
    drawing = get_object_or_404(Premeasure, pk=pm, project=project)
    added = fill_from_summary(project, drawing.summary or {"finishes": []})
    chosen = sum(1 for p in added if p.library_id)
    if added:
        messages.success(
            request,
            f"Added {len(added)} finish code{'s' if len(added) != 1 else ''} from {drawing.title}. "
            f"{chosen} matched a product in the list; choose a product for the rest, or add a new one.",
        )
    else:
        messages.success(request, "Every finish on that drawing is already in the setup.")
    url = reverse("setup", args=[pk])
    nxt = request.POST.get("next", "")
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        url += f"?next={nxt}"
    return redirect(url)


@login_required
@require_POST
def add_starter(request, pk: int):
    """Import a Callidus job: its products join the list, and it becomes this project's base job."""
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
        added, linked = import_from_job(project, est, legends, request.user)
        messages.success(
            request,
            f"Imported {est.filename}: {added} new product{'s' if added != 1 else ''} in the list, "
            f"{linked} finish code{'s' if linked != 1 else ''} matched.",
        )
    else:
        for errors in form.errors.values():
            for e in errors:
                messages.error(request, e)
    return redirect("setup", pk=pk)


@login_required
def project_rules(request, pk: int):
    project = get_object_or_404(Project, pk=pk)
    own = MeasuringRules.objects.filter(project=project).first()
    if request.method == "POST" and request.POST.get("action") == "use_company":
        if own:
            own.delete()
        messages.success(request, "This project now follows the company rules.")
        return redirect("project", pk=pk)
    if own is None:
        company = MeasuringRules.company()
        own_fields = {f.name: getattr(company, f.name) for f in MeasuringRules._meta.fields
                      if f.name not in ("id", "project", "updated_at")}
        instance = MeasuringRules(project=project, **own_fields)
    else:
        instance = own
    form = RulesForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        rules = form.save(commit=False)
        rules.project = project
        rules.save()
        messages.success(request, "Measuring rules saved for this project. Read a drawing again to apply them.")
        return redirect("project", pk=pk)
    return render(request, "jobs/rules.html", {"project": project, "form": form, "own": own,
                                               "applied": MeasuringRules.APPLIED})


# --- drawings ---------------------------------------------------------------


def _no_template_message(user) -> str:
    where = "Settings, Callidus template" if user.is_staff else "the administrator (Settings, Callidus template)"
    t = house_template()
    if t and not t.est:
        return f"The house Callidus template has a background but no Callidus job (.est). Add the .est in {where}."
    return f"There is no Callidus job to build on yet. Add the house Callidus template in {where}."


@login_required
def new_premeasure(request, pk: int):
    """Upload one or many drawings; they are read one after another in the background."""
    project = get_object_or_404(Project, pk=pk)
    job = base_job(project)
    if job is None:
        messages.error(request, _no_template_message(request.user))
        return redirect("project", pk=pk)
    form = UploadDrawingsForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        batch, single = start_drawings(project, form, request.user)
        if single is not None:
            return redirect("premeasure", pk=single.pk)
        return redirect("batch", pk=batch.pk)
    return render(request, "jobs/new_premeasure.html", {"project": project, "form": form})


def start_drawings(project: Project, form: UploadDrawingsForm, user, auto_write: bool | None = None):
    """Create the drawings for an upload and queue their reading. Returns the
    batch, and the drawing when only one single-page PDF was given."""
    job = base_job(project)
    d = form.cleaned_data
    multi = [f for f in d["drawings"] if form.pages[f.name] > 1]
    batch = Batch.objects.create(project=project, created_by=user,
                                 auto_write=d["auto_write"] if auto_write is None else auto_write,
                                 notify=d["notify"], files_pending=len(multi))
    # create every single-page drawing before any work starts, so the batch is never
    # seen as finished while some of it is still to come
    single, todo = None, []
    for f in d["drawings"]:
        upload = Upload.objects.create(
            project=project, kind=Upload.Kind.DRAWING, file=f,
            drawing_number=Path(f.name).stem[:100], revision=d["revision"],
        )
        if f in multi:
            todo.append(("jobs.tasks.split_pdf", upload.pk, batch.pk, d["level"], d.get("scale"), d.get("sheet") or ""))
            continue
        level = d["level"] if len(d["drawings"]) == 1 else ""
        title = " ".join(x for x in (project.code, level or Path(f.name).stem[:40]) if x)
        pm = Premeasure.objects.create(
            project=project, level=d["level"], title=safe_name(title), drawing=upload, page=1,
            starter=job.upload if job else None, created_by=user, batch=batch, status=Premeasure.Status.QUEUED,
            scale=d.get("scale"), sheet=d.get("sheet") or "",
        )
        single = pm if len(d["drawings"]) == 1 and not multi else None
        todo.append(("jobs.tasks.analyse_premeasure", pm.pk))
    for task in todo:
        enqueue(*task)
    return batch, single


@login_required
def batch(request, pk: int):
    b = get_object_or_404(Batch.objects.select_related("project"), pk=pk)
    drawings = list(b.premeasures.all())
    busy = any(d.busy for d in drawings) or not b.notified_at
    counts = {
        "done": sum(d.status == Premeasure.Status.DONE for d in drawings),
        "review": sum(d.status == Premeasure.Status.REVIEW for d in drawings),
        "failed": sum(d.status == Premeasure.Status.FAILED for d in drawings),
        "busy": sum(d.busy for d in drawings),
    }
    return render(request, "jobs/batch.html", {"batch": b, "project": b.project, "drawings": drawings,
                                               "busy": busy, "counts": counts})


@login_required
def batch_status(request, pk: int):
    b = get_object_or_404(Batch, pk=pk)
    drawings = list(b.premeasures.all())
    return JsonResponse({"busy": any(d.busy for d in drawings) or not b.notified_at,
                         "statuses": [d.status for d in drawings]})


def _mapping(pm: Premeasure) -> tuple[dict, dict]:
    """The project's products as they are now, with this drawing's own choices on top."""
    return drawing_mapping(pm)


def _rooms_view(pm: Premeasure, floors: dict) -> list[dict]:
    rooms = []
    for r in pm.summary["rooms"]:
        product = floors.get(r["finish"] or "", "")
        skirting = ", ".join(f"{k} {v:.2f} m" for k, v in sorted(r["skirting_m"].items()))
        rooms.append({**r, "product": product, "written": bool(product), "skirting": skirting})
    return rooms


@login_required
def premeasure(request, pk: int):
    from .tender import checks_for, rooms_to_check, written_out_of_date

    pm = get_object_or_404(Premeasure.objects.select_related("project", "drawing", "starter", "batch"), pk=pk)
    ctx = {"pm": pm, "project": pm.project}
    if pm.summary and not pm.busy:
        floors, skirtings = _mapping(pm)
        try:
            refresh_preview(pm, set(floors))
        except Exception:
            pass  # the picture is a convenience; never block the page on it
        form = ProductsForm(summary=pm.summary, floors=floors, skirtings=skirtings, project=pm.project)
        in_setup = {norm(p.finish_code) for p in pm.project.products.exclude(library=None)}
        rooms = _rooms_view(pm, floors)
        ctx.update(
            form=form,
            rooms=rooms,
            n_rooms=len(rooms),
            n_review=sum(r["status"] != "Verified" for r in rooms),
            area=round(sum(r["area_m2"] for r in rooms), 2),
            area_written=round(sum(r["area_m2"] for r in rooms if r["written"]), 2),
            n_written=sum(r["written"] for r in rooms),
            missing=[f for f in pm.summary["finishes"] if norm(f["code"]) not in in_setup],
            unmapped=unmapped(pm.summary, floors, skirtings),
            out_of_date=written_out_of_date(pm, floors, skirtings),
            to_check=len(rooms_to_check(pm, floors)),
            checks=checks_for(pm),
        )
        for r in rooms:
            c = ctx["checks"].get(r["id"])
            r["check"] = c.decision if c else ""
    ctx["sheets"] = ["A0", "A1", "A2", "A3", "A4"]
    return render(request, "jobs/premeasure.html", ctx)


@login_required
@require_POST
def write(request, pk: int):
    pm = get_object_or_404(Premeasure, pk=pk)
    if pm.busy or not pm.summary:
        return redirect("premeasure", pk=pk)
    form = ProductsForm(request.POST, summary=pm.summary, floors={}, skirtings={}, project=pm.project)
    if not form.is_valid():
        messages.error(request, "One of the products is no longer in the product list. Please choose again.")
        return redirect("premeasure", pk=pk)
    floors, skirtings = form.chosen()
    added = keep_in_project(pm, floors, skirtings)
    pm.chosen_here = chosen_differently(pm, floors, skirtings)
    if request.POST.get("action") == "save":  # keep the choices, write later
        pm.save(update_fields=["chosen_here", "updated_at"])
        messages.success(request, "Products saved." + (
            f" {', '.join(added)} added to the project's products." if added else ""))
        return redirect("premeasure", pk=pk)
    pm.finish_products, pm.skirting_products = floors, skirtings
    if not pm.finish_products:
        pm.save(update_fields=["finish_products", "skirting_products", "chosen_here", "updated_at"])
        messages.error(request, "Choose a product for at least one finish.")
        return redirect("premeasure", pk=pk)
    pm.status, pm.error = Premeasure.Status.WRITING, ""
    pm.save()
    enqueue("jobs.tasks.write_premeasure", pm.pk)
    return redirect("premeasure", pk=pk)


@login_required
@require_POST
def reread(request, pk: int):
    """Read the drawing again, for example after the measuring rules change."""
    pm = get_object_or_404(Premeasure, pk=pk)
    if not pm.busy:
        if pm.analysis_file:
            pm.analysis_file.delete(save=False)
        pm.status = Premeasure.Status.QUEUED
        pm.save()
        enqueue("jobs.tasks.analyse_premeasure", pm.pk)
    return redirect("premeasure", pk=pk)


@login_required
@require_POST
def set_scale(request, pk: int):
    """The sheet has no scale printed: the estimator gives it, and the drawing is read again."""
    pm = get_object_or_404(Premeasure, pk=pk)
    form = ScaleForm(request.POST)
    if form.is_valid() and not pm.busy:
        pm.scale = form.cleaned_data["scale"]
        if form.cleaned_data.get("sheet"):
            pm.sheet = form.cleaned_data["sheet"]
        pm.status, pm.error = Premeasure.Status.QUEUED, ""
        pm.save()
        enqueue("jobs.tasks.analyse_premeasure", pm.pk)
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
            pm.status = Premeasure.Status.QUEUED
            pm.save(update_fields=["status", "updated_at"])
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


# --- product list -----------------------------------------------------------


@login_required
def products(request):
    q = request.GET.get("q", "").strip()
    kind = request.GET.get("type", "")
    items = LibraryProduct.objects.annotate(n_projects=Count("uses__project", distinct=True))
    if q:
        items = items.filter(Q(name__icontains=q) | Q(supplier__icontains=q) | Q(product_number__icontains=q)
                             | Q(colour_name__icontains=q))
    if kind:
        items = items.filter(type=kind)
    return render(request, "jobs/products.html", {"items": items, "q": q, "type": kind,
                                                  "types": LibraryProduct.Type.choices})


@login_required
def product_edit(request, pk: int | None = None):
    lib = get_object_or_404(LibraryProduct, pk=pk) if pk else None
    choices = [e.name for e in base_products(house_job()) if e.copyable]
    initial = {}
    if lib is None:
        # "New product" from a project setup row: start from what the drawing's legend says
        for key in ("name", "type", "fill"):
            if request.GET.get(key):
                initial[key] = request.GET[key]
    form = LibraryProductForm(request.POST or None, instance=lib, initial=initial, settings_choices=choices)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        if lib is None:
            obj.created_by = request.user
        obj.save()
        row = request.GET.get("row")
        if row and row.isdigit():  # assign the new product to the setup row it was made for
            Product.objects.filter(pk=int(row), library=None).update(library=obj)
        messages.success(request, f"Saved {obj.name}.")
        return _back(request, request.GET.get("next"), reverse("products"))
    return render(request, "jobs/product_edit.html", {
        "form": form, "lib": lib, "next": request.GET.get("next", ""),
        "uses": lib.uses.select_related("project") if lib else [],
    })


# --- settings ---------------------------------------------------------------


@staff_required
def settings_home(request):
    return render(request, "jobs/settings.html", {
        "template": house_template(),
        "n_users": User.objects.filter(is_active=True).count(),
        "n_products": LibraryProduct.objects.filter(active=True).count(),
    })


@staff_required
def settings_rules(request):
    form = RulesForm(request.POST or None, instance=MeasuringRules.company())
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Company measuring rules saved. Projects with their own rules keep them.")
        return redirect("settings")
    return render(request, "jobs/rules.html", {"form": form, "company": True, "applied": MeasuringRules.APPLIED})


@staff_required
def settings_template(request):
    t = house_template()
    form = TemplateForm(request.POST or None, request.FILES or None, instance=t)
    if request.method == "POST" and form.is_valid():
        if not (form.cleaned_data.get("est") or (t and t.est)):
            form.add_error("est", "Add the Callidus job (.est).")
        else:
            form.save()
            if getattr(form, "warn_no_edge", False):
                messages.warning(request, "Saved. This job has no coved skirting product, so new skirting "
                                          "products cannot be added to Callidus until it has one.")
            else:
                messages.success(request, "Callidus template saved.")
            return redirect("settings")
    return render(request, "jobs/template.html", {"form": form, "template": t,
                                                  "products": base_products(house_job())})


@staff_required
def settings_users(request):
    form = UserForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        messages.success(request, f"Added {user.get_full_name() or user.username}. Send them the sign-in "
                                  f"address and password separately.")
        return redirect("settings_users")
    return render(request, "jobs/users.html", {"users": User.objects.order_by("-is_active", "first_name"),
                                               "form": form})


@staff_required
def settings_user(request, pk: int):
    account = get_object_or_404(User, pk=pk)
    form = UserForm(request.POST or None, instance=account)
    if request.method == "POST" and form.is_valid():
        if account == request.user and not (form.cleaned_data["is_staff"] and form.cleaned_data["is_active"]):
            form.add_error(None, "You cannot remove your own access to settings.")
        else:
            form.save()
            messages.success(request, "Saved.")
            return redirect("settings_users")
    return render(request, "jobs/user_edit.html", {"form": form, "account": account})
