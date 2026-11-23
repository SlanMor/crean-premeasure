import tempfile
from pathlib import Path

from django import forms

from django.contrib.auth.models import User

from .models import BackgroundTemplate, Company, LibraryProduct, MeasuringRules, Product, Project, RoomCheck


def _check_file(upload, suffix: str, check) -> None:
    """Run an engine check on an uploaded file before it is saved."""
    if not upload.name.lower().endswith(suffix.lower()):
        raise forms.ValidationError(f"Expected a {suffix} file.")
    with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
        for chunk in upload.chunks():
            tmp.write(chunk)
        tmp.flush()
        try:
            return check(Path(tmp.name))
        except Exception as exc:
            raise forms.ValidationError(f"This file could not be read ({exc}).") from exc
        finally:
            upload.seek(0)


SHEET_CHOICES = [("", "Read from the drawing")] + [(x, x) for x in ("A0", "A1", "A2", "A3", "A4")]


NEW = "__new__"


class CompanyChoice(forms.ChoiceField):
    """A company of one kind from the list, or a new one typed in next to it."""

    def __init__(self, kind: str, label: str, **kwargs):
        self.kind = kind
        super().__init__(label=label, required=False, choices=self._choices, **kwargs)

    def _choices(self):
        return ([("", "Not set")]
                + [(str(c.pk), c.name) for c in Company.objects.filter(kind=self.kind, active=True)]
                + [(NEW, f"Add a new {Company.Kind(self.kind).label.lower()}...")])


class ProjectForm(forms.ModelForm):
    """Tender details, with the client, main contractor and architect picked from
    the company list (or added to it here)."""

    client = CompanyChoice("client", "Client")
    client_new = forms.CharField(label="New client", max_length=200, required=False)
    main_contractor = CompanyChoice("contractor", "Main contractor")
    main_contractor_new = forms.CharField(label="New main contractor", max_length=200, required=False)
    architect = CompanyChoice("architect", "Architect")
    architect_new = forms.CharField(label="New architect", max_length=200, required=False)

    PICKERS = (("client", "client"), ("main_contractor", "contractor"), ("architect", "architect"))

    class Meta:
        model = Project
        fields = ["code", "name", "site", "due", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3}), "due": forms.DateInput(attrs={"type": "date"})}
        labels = {"code": "Project ID", "name": "Project name"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field, _ in self.PICKERS:
            co = getattr(self.instance, field, None) if self.instance.pk else None
            if co is not None:
                self.initial[field] = str(co.pk)

    def clean(self):
        data = super().clean()
        for field, kind in self.PICKERS:
            choice, typed = data.get(field) or "", (data.get(f"{field}_new") or "").strip()
            if choice == NEW and not typed:
                self.add_error(f"{field}_new", "Type the name.")
            elif choice == NEW:
                data[field] = ("new", kind, typed)
            elif choice:
                data[field] = Company.objects.filter(pk=int(choice), kind=kind).first()
            else:
                data[field] = None
        return data

    def save(self, commit=True):
        project = super().save(commit=False)
        for field, _ in self.PICKERS:
            value = self.cleaned_data.get(field)
            if isinstance(value, tuple):
                existing = Company.objects.filter(kind=value[1], name__iexact=value[2]).first()
                value = existing or Company.objects.create(kind=value[1], name=value[2])
            setattr(project, field, value)
        if commit:
            project.save()
            self.save_m2m()
        return project


class CompanyForm(forms.ModelForm):
    class Meta:
        model = Company
        fields = ["kind", "name", "contact_name", "email", "phone", "notes", "active"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}


class TenderQuestionsForm(forms.ModelForm):
    """The measuring rules, started from the company's: change only what this tender does differently."""

    class Meta:
        model = MeasuringRules
        exclude = ["project", "updated_at"]


class RoomCheckForm(forms.Form):
    decision = forms.ChoiceField(choices=RoomCheck.Decision.choices)
    note = forms.CharField(max_length=300, required=False)


class StarterForm(forms.Form):
    est = forms.FileField(
        label="Callidus job (.est)",
        help_text="A Callidus job for this tender with products already set up. Its products are added to the "
        "setup and its rooms are not used.",
    )
    calback = forms.FileField(
        label="Its background (.CalBack)", required=False,
        help_text="Optional. The .CalBack saved next to the .est.",
    )

    def clean_est(self):
        from premeasure_engine.callidus import read_est

        f = self.cleaned_data["est"]
        est = _check_file(f, ".est", read_est)
        products = [m for m in est.materials if m.name != "NONE"]
        if not products:
            raise forms.ValidationError("No products found in this file. Is it a Callidus job with its products set up?")
        self.products = len(products)
        return f

    def clean_calback(self):
        from premeasure_engine.premeasure import calback_sheets

        f = self.cleaned_data.get("calback")
        if f:
            _check_file(f, ".CalBack", calback_sheets)
        return f


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput(attrs={"accept": ".pdf"}))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single = super().clean
        if isinstance(data, (list, tuple)):
            return [single(d, initial) for d in data]
        return [single(data, initial)]


class UploadDrawingsForm(forms.Form):
    drawings = MultipleFileField(
        label="Finishes drawings (PDF)",
        help_text="Choose one or several. A PDF with several pages is fine: each page that looks like a "
        "finishes plan is read.",
    )
    level = forms.CharField(max_length=100, required=False, help_text="Optional, for example Ground floor.")
    revision = forms.CharField(max_length=30, required=False)
    scale = forms.IntegerField(
        label="Scale 1:", required=False, min_value=1, max_value=5000,
        help_text="Leave blank: it is read from the drawing. Only fill in if the sheet has no scale printed.",
    )
    sheet = forms.ChoiceField(
        label="Sheet size", required=False, choices=SHEET_CHOICES,
        help_text="Leave blank: only needed for a scanned drawing (a picture, not the architect's export) "
        "when the title block does not show it. Measuring a picture depends on it.",
    )
    auto_write = forms.BooleanField(
        label="Write the Callidus files automatically", required=False, initial=True,
        help_text="For each drawing where every finish has a product in the project setup. "
        "The others wait for you to review.",
    )
    notify = forms.BooleanField(label="Email me when they are all done", required=False, initial=True)

    def clean_drawings(self):
        from premeasure_engine.premeasure import pdf_pages

        files = self.cleaned_data["drawings"]
        self.pages = {}
        for f in files:
            try:
                self.pages[f.name] = _check_file(f, ".pdf", pdf_pages)
            except forms.ValidationError as exc:
                raise forms.ValidationError(f"{f.name}: {exc.messages[0]}") from exc
        return files


def _grouped(none: str, names: list[str], mine: set[str]) -> list:
    """Choices with this project's products first, then the rest of the list."""
    first = [(n, n) for n in names if n in mine]
    rest = [(n, n) for n in names if n not in mine]
    if not first:
        return [("", none)] + rest
    return [("", none), ("This project's products", first), ("All products", rest)]


class ProductsForm(forms.Form):
    """One choice per finish on the drawing: a product from the list, or leave out.
    The project setup gives the starting choice."""

    LEAVE_OUT = ""

    def __init__(self, *args, summary: dict, floors: dict, skirtings: dict, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        products = list(LibraryProduct.objects.filter(active=True).values_list("name", "type"))
        mine = set(project.project_products().values_list("name", flat=True)) if project else set()
        for f in summary["finishes"]:
            floor = f["kind"] == "floor"
            names = [n for n, t in products if (t in LibraryProduct.FLOOR_TYPES) == floor]
            none = "Leave out (not priced)" if floor else "Not on room edges"
            initial = (floors if floor else skirtings).get(f["code"], "")
            if initial and initial not in names:
                names = names + [initial]
            self.fields[self.key(f)] = forms.ChoiceField(
                choices=_grouped(none, names, mine), required=False, initial=initial, label=f["code"],
            )
        self.finishes = summary["finishes"]

    @staticmethod
    def key(f: dict) -> str:
        return f"{f['kind']}:{f['code']}"

    def rows(self):
        for f in self.finishes:
            yield f, self[self.key(f)]

    def chosen(self) -> tuple[dict, dict]:
        floors, skirtings = {}, {}
        for f in self.finishes:
            value = self.cleaned_data.get(self.key(f))
            if value:
                (floors if f["kind"] == "floor" else skirtings)[f["code"]] = value
        return floors, skirtings


class SetupRowForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ["finish_code", "library"]

    def __init__(self, *args, mine: set | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["library"].queryset = LibraryProduct.objects.filter(active=True)
        if mine:  # this project's products first
            items = list(self.fields["library"].queryset)
            first = [(p.pk, p.name) for p in items if p.pk in mine]
            rest = [(p.pk, p.name) for p in items if p.pk not in mine]
            self.fields["library"].choices = [("", "Not chosen yet"), ("This project's products", first),
                                              ("All products", rest)]
        self.fields["library"].required = False
        self.fields["library"].empty_label = "Not chosen yet"
        self.fields["finish_code"].widget.attrs["placeholder"] = "code"


class _SetupBase(forms.BaseModelFormSet):
    def clean(self):
        super().clean()
        from .setup import norm

        seen: set[str] = set()
        for f in self.forms:
            if not hasattr(f, "cleaned_data") or f in self.deleted_forms:
                continue
            code = f.cleaned_data.get("finish_code") or ""
            if not code:
                continue
            if norm(code) in seen:
                raise forms.ValidationError(f"Finish code {code} is used twice.")
            seen.add(norm(code))


SetupFormSet = forms.modelformset_factory(Product, form=SetupRowForm, formset=_SetupBase, extra=2, can_delete=True)


class LibraryProductForm(forms.ModelForm):
    class Meta:
        model = LibraryProduct
        fields = ["name", "type", "supplier", "product_number", "colour_name", "fill", "roll_width_m",
                  "tile_width_mm", "tile_length_mm", "coving_height_mm", "wastage_pct", "round_up",
                  "spec_reference", "notes", "callidus_settings_from", "active"]
        widgets = {"fill": forms.TextInput(attrs={"type": "color"}), "notes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, settings_choices: list[str] = (), **kwargs):
        super().__init__(*args, **kwargs)
        current = self.instance.callidus_settings_from if self.instance.pk else ""
        names = list(settings_choices) + ([current] if current and current not in settings_choices else [])
        self.fields["callidus_settings_from"] = forms.ChoiceField(
            choices=[("", "Automatic, by type")] + [(n, n) for n in names], required=False,
            label="Callidus settings copied from",
            help_text="Only for a product that is not already in the Callidus template.",
        )
        self.fields["fill"].required = False
        if not self.instance.fill and not self.initial.get("fill"):
            self.initial["fill"] = "#c8c8c8"  # a colour box cannot be empty: grey, not black


class RulesForm(forms.ModelForm):
    class Meta:
        model = MeasuringRules
        exclude = ["project", "updated_at"]


class TemplateForm(forms.ModelForm):
    class Meta:
        model = BackgroundTemplate
        fields = ["est", "file", "note"]
        labels = {"file": "Background (.CalBack)"}

    def clean_est(self):
        from premeasure_engine.callidus.material_writer import existing_products

        f = self.cleaned_data.get("est")
        if f and hasattr(f, "chunks"):
            products = _check_file(f, ".est", existing_products)
            kinds = {e.kind for e in products if e.copyable}
            if "sheet" not in kinds:
                raise forms.ValidationError("This job has no sheet product that new products can copy.")
            self.warn_no_edge = "edge" not in kinds
        return f

    def clean_file(self):
        from premeasure_engine.premeasure import calback_sheets

        f = self.cleaned_data.get("file")
        if f and hasattr(f, "chunks"):
            n = _check_file(f, ".CalBack", calback_sheets)
            if n != 1:
                raise forms.ValidationError(f"This background has {n} sheets; the template needs one with one sheet.")
        return f


class UserForm(forms.ModelForm):
    password = forms.CharField(
        widget=forms.PasswordInput, required=False, min_length=12,
        help_text="At least 12 characters. Leave blank to keep the current password.",
    )
    is_staff = forms.BooleanField(label="Can change settings", required=False,
                                  help_text="Users, the Callidus template and company measuring rules.")

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email", "is_staff", "is_active"]
        labels = {"is_active": "Can sign in"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            self.fields["password"].required = True
            self.fields["password"].help_text = "At least 12 characters."
            self.fields["email"].required = True

    def save(self, commit=True):
        user = super().save(commit=False)
        if self.cleaned_data.get("password"):
            user.set_password(self.cleaned_data["password"])
        if commit:
            user.save()
        return user


class ScaleForm(forms.Form):
    scale = forms.IntegerField(label="Scale 1:", min_value=1, max_value=5000, initial=100)
    sheet = forms.ChoiceField(label="Sheet size", required=False, choices=SHEET_CHOICES)


class ShortlistForm(forms.Form):
    add = forms.ModelMultipleChoiceField(
        queryset=LibraryProduct.objects.filter(active=True), required=False,
        label="Add products from the list",
    )
