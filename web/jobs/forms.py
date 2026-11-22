import tempfile
from pathlib import Path

from django import forms

from .models import Product, Project, Upload


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


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ["code", "name", "client", "main_contractor", "site", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}


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


class PremeasureForm(forms.Form):
    drawing = forms.FileField(label="Finishes drawing (PDF)")
    page = forms.IntegerField(min_value=1, initial=1, help_text="Page of the PDF with the floor plan.")
    level = forms.CharField(max_length=100, required=False, help_text="For example Ground floor.")
    revision = forms.CharField(max_length=30, required=False)
    title = forms.CharField(
        max_length=120, required=False, label="Name for the Callidus file",
        help_text="Leave blank to use the project code, level and drawing name.",
    )

    def clean_drawing(self):
        from premeasure_engine.premeasure import pdf_pages

        f = self.cleaned_data["drawing"]
        self.pages = _check_file(f, ".pdf", pdf_pages)
        return f

    def clean(self):
        data = super().clean()
        pages = getattr(self, "pages", None)
        if pages and data.get("page") and data["page"] > pages:
            self.add_error("page", f"The PDF has {pages} page{'s' if pages > 1 else ''}.")
        return data


class ProductsForm(forms.Form):
    """One choice per finish on the drawing: a project product, or leave out."""

    LEAVE_OUT = ""

    def __init__(self, *args, summary: dict, floors: dict, skirtings: dict, materials: list[str], **kwargs):
        super().__init__(*args, **kwargs)
        for f in summary["finishes"]:
            if f["kind"] == "floor":
                none = "Leave out (not priced)"
                initial = floors.get(f["code"], "")
            else:
                none = "Not on room edges"
                initial = skirtings.get(f["code"], "")
            self.fields[self.key(f)] = forms.ChoiceField(
                choices=[(self.LEAVE_OUT, none)] + [(m, m) for m in materials],
                required=False, initial=initial, label=f["code"],
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


DEFAULT_FILL = "#c8c8c8"


class SetupProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ["finish_code", "kind", "name", "fill", "settings_from"]
        widgets = {"fill": forms.TextInput(attrs={"type": "color"})}

    def __init__(self, *args, settings_choices: list[str] = (), existing: set[str] = frozenset(), **kwargs):
        super().__init__(*args, **kwargs)
        self.existing = existing
        current = self.instance.settings_from if self.instance and self.instance.pk else ""
        names = list(settings_choices) + ([current] if current and current not in settings_choices else [])
        self.fields["settings_from"] = forms.ChoiceField(
            choices=[("", "Choose")] + [(n, n) for n in names], required=False, label="Copy settings from"
        )
        self.fields["finish_code"].widget.attrs["placeholder"] = "code"
        self.fields["fill"].required = False
        # a colour box cannot be empty, so show grey rather than black for "no colour yet"
        if not (self.instance and self.instance.fill):
            self.initial["fill"] = DEFAULT_FILL

    @property
    def in_job(self) -> bool:
        return bool(self.instance and self.instance.name in self.existing)

    def clean_fill(self):
        v = self.cleaned_data.get("fill") or ""
        return v if v == "" or (len(v) == 7 and v.startswith("#")) else ""


class _SetupBase(forms.BaseModelFormSet):
    def clean(self):
        super().clean()
        from .setup import norm

        seen: dict[str, str] = {}
        names: set[str] = set()
        for f in self.forms:
            if not hasattr(f, "cleaned_data") or f in self.deleted_forms or not f.cleaned_data.get("name"):
                continue
            code, name = f.cleaned_data.get("finish_code") or "", f.cleaned_data["name"]
            if code:
                if norm(code) in seen:
                    raise forms.ValidationError(f"Finish code {code} is used twice ({seen[norm(code)]} and {name}).")
                seen[norm(code)] = name
            if name in names:
                raise forms.ValidationError(f"Two products are called {name!r}.")
            names.add(name)


SetupFormSet = forms.modelformset_factory(
    Product, form=SetupProductForm, formset=_SetupBase, extra=2, can_delete=True
)
