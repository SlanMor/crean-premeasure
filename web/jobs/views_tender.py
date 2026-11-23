"""The new tender wizard, the tender's to-do list, room checks, queries for the
architect, tender quantities, and the company list."""

from pathlib import Path

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .forms import CompanyForm, ProjectForm, SetupFormSet, ShortlistForm, TenderQuestionsForm, UploadDrawingsForm
from .models import Company, MeasuringRules, Premeasure, Project, RoomCheck
from .setup import base_job, drawing_mapping, fill_from_summary
from .tender import (
    checks_for,
    codes_in_tender,
    query_email,
    queries,
    rooms_to_check,
    suggest_from_history,
    tender_todo,
    tender_workbook,
    write_ready,
    zoom_page_path,
)
from .setup import norm

staff_required = user_passes_test(lambda u: u.is_active and u.is_staff)

STEPS = [("details", "Tender details"), ("rules", "Tender questions"), ("drawings", "Drawings"),
         ("products", "Products"), ("done", "Run")]


def _steps(project, current: str) -> list[dict]:
    order = [k for k, _ in STEPS]
    reached = order.index(project.setup_step) if project and project.setup_step in order else 0
    if project and project.setup_step == Project.Step.DONE:
        reached = len(order) - 1
    return [{"key": k, "label": label, "n": i + 1, "current": k == current,
             "done": i < reached or (project is not None and k == "details"),
             "url": reverse("wizard", args=[project.pk, k]) if project and k != "details"
             else (reverse("project_edit", args=[project.pk]) if project else "")}
            for i, (k, label) in enumerate(STEPS)]


# --- wizard -------------------------------------------------------------------------


@login_required
def new_tender(request):
    """Step 1: who the tender is for. Clients, contractors and architects come from the list."""
    form = ProjectForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        project = form.save(commit=False)
        project.setup_step = Project.Step.RULES
        project.save()
        return redirect("wizard", pk=project.pk, step="rules")
    return render(request, "jobs/wizard_details.html", {"form": form, "steps": _steps(None, "details"),
                                                        "company_kinds": _kinds()})


def _kinds():
    return {"client": "client", "main_contractor": "main contractor", "architect": "architect"}


@login_required
def project_edit(request, pk: int):
    project = get_object_or_404(Project, pk=pk)
    form = ProjectForm(request.POST or None, instance=project)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Tender details saved.")
        if project.setup_step != Project.Step.DONE:
            return redirect("wizard", pk=pk, step=project.setup_step)
        return redirect("project", pk=pk)
    return render(request, "jobs/wizard_details.html", {"form": form, "project": project,
                                                        "steps": _steps(project, "details") if project.setup_step != "done" else None})


def _advance(project: Project, to: str) -> None:
    order = [k for k, _ in STEPS]
    if project.setup_step != Project.Step.DONE and order.index(to) > order.index(project.setup_step):
        project.setup_step = to
        project.save(update_fields=["setup_step"])


@login_required
def wizard(request, pk: int, step: str):
    project = get_object_or_404(Project, pk=pk)
    handler = {"rules": _wizard_rules, "drawings": _wizard_drawings, "products": _wizard_products,
               "done": _wizard_done}.get(step)
    if handler is None:
        raise Http404
    return handler(request, project)


def _wizard_rules(request, project):
    """Step 2: the measuring rules, starting from the company's. Saved as the
    project's own only where they differ."""
    company = MeasuringRules.company()
    own = MeasuringRules.objects.filter(project=project).first()
    fields = [f.name for f in MeasuringRules._meta.fields if f.name not in ("id", "project", "updated_at")]
    instance = own or MeasuringRules(project=project, **{f: getattr(company, f) for f in fields})
    form = TenderQuestionsForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        rules = form.save(commit=False)
        differs = [f for f in fields if getattr(rules, f) != getattr(company, f)]
        if differs:
            rules.project = project
            rules.save()
            messages.success(request, f"{len(differs)} rule{'s' if len(differs) != 1 else ''} set differently for this tender.")
        elif own:
            own.delete()
        _advance(project, "drawings")
        return redirect("wizard", pk=project.pk, step="drawings")
    return render(request, "jobs/wizard_rules.html", {
        "project": project, "form": form, "steps": _steps(project, "rules"),
        "applied": MeasuringRules.APPLIED, "company": company,
    })


def _wizard_drawings(request, project):
    """Step 3: drop in the drawing pack. Reading starts straight away; products come next."""
    from .views import start_drawings

    form = UploadDrawingsForm(request.POST or None, request.FILES or None, initial={"auto_write": False})
    if request.method == "POST":
        if request.POST.get("skip"):
            _advance(project, "products")
            return redirect("wizard", pk=project.pk, step="products")
        if base_job(project) is None:
            messages.error(request, "There is no Callidus job to build on yet. Ask the administrator to add the "
                                    "house Callidus template (Settings, Callidus template).")
        elif form.is_valid():
            start_drawings(project, form, request.user, auto_write=False)
            _advance(project, "products")
            return redirect("wizard", pk=project.pk, step="products")
    return render(request, "jobs/wizard_drawings.html", {
        "project": project, "form": form, "steps": _steps(project, "drawings"),
        "drawings": project.premeasures.all(), "job": base_job(project),
    })


def _wizard_products(request, project):
    """Step 4: one table for the whole tender. Codes from every legend read so far,
    with a product suggested where one clearly matches."""
    drawings = list(project.premeasures.all())
    busy = [d for d in drawings if d.busy]
    if request.method == "GET":
        for d in drawings:
            if d.summary and not d.busy:
                fill_from_summary(project, d.summary)
        remembered = suggest_from_history(project)
        if remembered:
            messages.info(request, f"{len(remembered)} product{'s' if len(remembered) != 1 else ''} taken from "
                                   "earlier tenders with the same architect or client: check them.")
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
            obj.spec_source = obj.spec_source or "Chosen in the tender set-up"
            obj.save()
        project.shortlist.add(*shortlist.cleaned_data["add"])
        messages.success(request, "Products saved.")
        if request.POST.get("new_for"):
            from .views import _new_product_url

            return redirect(_new_product_url(project, request.POST["new_for"], request.get_full_path()))
        if request.POST.get("stay"):
            return redirect("wizard", pk=project.pk, step="products")
        _advance(project, "done")
        return redirect("wizard", pk=project.pk, step="done")
    uses = codes_in_tender(project)
    from .tender import natural

    rows = [(f, uses.get(norm(f.instance.finish_code))) for f in formset.forms]
    rows.sort(key=lambda fu: (not fu[0].instance.pk, fu[1].kind == "skirting" if fu[1] else False,
                              natural(fu[0].instance.finish_code or "")))
    return render(request, "jobs/wizard_products.html", {
        "project": project, "formset": formset, "rows": rows, "shortlist_form": shortlist,
        "shortlist": project.shortlist.filter(active=True), "steps": _steps(project, "products"),
        "busy": busy, "n_drawings": len(drawings), "n_read": sum(1 for d in drawings if d.summary and not d.busy),
        "conflicts": [u for u in uses.values() if u.conflict],
    })


def _wizard_done(request, project):
    """Step 5: write every drawing that is ready, and hand over to the to-do list."""
    if request.method == "POST":
        queued = write_ready(project)
        project.setup_step = Project.Step.DONE
        project.save(update_fields=["setup_step"])
        messages.success(request, f"Writing {len(queued)} drawing{'s' if len(queued) != 1 else ''}. "
                                  "The list below shows what is left." if queued else "Set-up finished.")
        return redirect("project", pk=project.pk)
    return render(request, "jobs/wizard_done.html", {
        "project": project, "steps": _steps(project, "done"), "todo": tender_todo(project, request.user),
    })


# --- the tender's own pages ---------------------------------------------------------


@login_required
@require_POST
def write_ready_view(request, pk: int):
    project = get_object_or_404(Project, pk=pk)
    queued = write_ready(project)
    if queued:
        messages.success(request, f"Writing {len(queued)} drawing{'s' if len(queued) != 1 else ''}.")
    else:
        messages.info(request, "No drawing is ready to write: each needs a product for every finish.")
    return redirect("project", pk=pk)


@login_required
def queries_view(request, pk: int):
    project = get_object_or_404(Project, pk=pk)
    items = queries(project)
    subject, body = query_email(project, items)
    return render(request, "jobs/queries.html", {"project": project, "items": items, "subject": subject,
                                                 "body": body, "to": project.architect.email if project.architect else ""})


@login_required
def tender_quantities(request, pk: int):
    from .tasks import safe_name

    project = get_object_or_404(Project, pk=pk)
    data = tender_workbook(project)
    resp = HttpResponse(data, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f'attachment; filename="{safe_name(project.code + " " + project.name)} - quantities.xlsx"'
    return resp


@login_required
def todo_status(request, pk: int):
    project = get_object_or_404(Project, pk=pk)
    return JsonResponse({"busy": project.premeasures.filter(status__in=["queued", "reading", "writing"]).exists()})


# --- room checks --------------------------------------------------------------------


@login_required
def check_tender(request, pk: int):
    """Start checking rooms on the first drawing that has some left."""
    project = get_object_or_404(Project, pk=pk)
    for d in project.premeasures.exclude(summary=None).order_by("created_at"):
        if not d.busy and rooms_to_check(d):
            return redirect("check", pk=d.pk)
    messages.success(request, "No rooms left to check.")
    return redirect("project", pk=pk)


EXPLAIN = [
    ("opening", "There is a hole in the floor (a riser, column or void) bigger than the size measured through. "
                "Callidus counts the whole outline, so check the area deducted."),
    ("picture", "The drawing was a scan, so the outline was found from colours. Check it runs along the walls."),
    ("printed", "The area printed on the drawing does not match the area measured. Rooms sharing a finish can run "
                "together through a doorway."),
    ("stair", "Stairs: the plan area is measured, not treads and risers."),
    ("merged", "Several rooms with the same finish joined into one area, usually through open doorways."),
    ("no room tag", "No room number was found inside the area."),
]


@login_required
def check(request, pk: int):
    """One room at a time, biggest first: is the outline right?"""
    pm = get_object_or_404(Premeasure.objects.select_related("project"), pk=pk)
    if not pm.summary or pm.busy:
        return redirect("premeasure", pk=pk)
    floors, _ = drawing_mapping(pm)
    if request.method == "POST":
        room_id = int(request.POST["room"])
        decision = request.POST.get("decision")
        if decision == "undo":
            pm.checks.filter(room_id=room_id).delete()
            return redirect(f"{reverse('check', args=[pk])}?room={room_id}")
        if decision in RoomCheck.Decision.values and any(r["id"] == room_id for r in pm.summary["rooms"]):
            RoomCheck.objects.update_or_create(
                premeasure=pm, room_id=room_id,
                defaults={"decision": decision, "note": request.POST.get("note", "")[:300], "by": request.user},
            )
        return redirect("check", pk=pk)
    todo = rooms_to_check(pm, floors)
    rooms = {r["id"]: r for r in pm.summary["rooms"]}
    want = request.GET.get("room")
    room = rooms.get(int(want)) if want and want.isdigit() else (todo[0] if todo else None)
    done = checks_for(pm)
    nxt = None
    if room is None:
        for d in pm.project.premeasures.exclude(summary=None).exclude(pk=pm.pk).order_by("created_at"):
            if not d.busy and rooms_to_check(d):
                nxt = d
                break
    total = len(todo) + sum(1 for c in done.values())
    ids = [r["id"] for r in todo]
    skip = None
    if room is not None and len(ids) > 1:
        skip = ids[(ids.index(room["id"]) + 1) % len(ids)] if room["id"] in ids else ids[0]
    return render(request, "jobs/check.html", {
        "pm": pm, "project": pm.project, "room": room, "left": len(todo), "total": total,
        "product": floors.get(room["finish"] or "", "") if room else "", "check": done.get(room["id"]) if room else None,
        "next_drawing": nxt,
        "recent": [(c, rooms.get(c.room_id, {})) for c in sorted(done.values(), key=lambda c: c.at, reverse=True)[:8]],
        "skip": skip, "explain": EXPLAIN,
    })


@login_required
def room_image(request, pk: int, room: int):
    """A close-up of one room, drawn from the drawing (the page is rendered once and kept)."""
    import tempfile

    from PIL import Image
    from premeasure_engine.premeasure import _page_image, render_room

    from .tasks import _analysis

    pm = get_object_or_404(Premeasure, pk=pk)
    if not pm.summary or room >= len(pm.summary["rooms"]):
        raise Http404
    a = _analysis(pm)
    page_path = zoom_page_path(pm)
    if page_path.exists():
        Image.MAX_IMAGE_PIXELS = None
        page = Image.open(page_path)
    else:
        page = _page_image(a, 100).convert("L")
        page_path.parent.mkdir(parents=True, exist_ok=True)
        page.save(page_path, quality=85)
    with tempfile.TemporaryDirectory() as tmp:
        png = render_room(a, room, Path(tmp) / "room.png", page=page, dpi=100)
        data = png.read_bytes()
    resp = HttpResponse(data, content_type="image/png")
    resp["Cache-Control"] = "private, max-age=3600"
    return resp


# --- companies ----------------------------------------------------------------------


@staff_required
def companies(request):
    kind = request.GET.get("kind", "")
    items = Company.objects.all()
    if kind:
        items = items.filter(kind=kind)
    from django.db.models import Count

    items = items.annotate(n=Count("client_of", distinct=True) + Count("contractor_of", distinct=True)
                           + Count("architect_of", distinct=True))
    return render(request, "jobs/companies.html", {"items": items, "kind": kind, "kinds": Company.Kind.choices})


@staff_required
def company_edit(request, pk: int | None = None):
    co = get_object_or_404(Company, pk=pk) if pk else None
    form = CompanyForm(request.POST or None, instance=co, initial={"kind": request.GET.get("kind", "client")})
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Saved.")
        return redirect(f"{reverse('companies')}?kind={form.instance.kind}")
    return render(request, "jobs/company_edit.html", {"form": form, "co": co})


@staff_required
def feedback(request):
    """Rooms the estimators said were wrong: where the reading needs work."""
    items = RoomCheck.objects.filter(decision=RoomCheck.Decision.BY_HAND).select_related("premeasure__project", "by") \
        .order_by("-at")[:300]
    rows = []
    for c in items:
        rooms = {r["id"]: r for r in (c.premeasure.summary or {}).get("rooms", [])}
        rows.append((c, rooms.get(c.room_id)))
    return render(request, "jobs/feedback.html", {"rows": rows})
