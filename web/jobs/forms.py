import tempfile
from pathlib import Path

from django import forms

from .models import Project, Upload


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
        help_text="Any Callidus job for this project that already has its products set up. Its rooms are not used.",
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
    starter = forms.ModelChoiceField(
        queryset=Upload.objects.none(), label="Products from",
        help_text="The Callidus job whose products this drawing is measured in.",
    )
    title = forms.CharField(
        max_length=120, required=False, label="Name for the Callidus file",
        help_text="Leave blank to use the project code, level and drawing name.",
    )

    def __init__(self, *args, project: Project, **kwargs):
        super().__init__(*args, **kwargs)
        qs = project.uploads.filter(kind=Upload.Kind.CALLIDUS_EST).order_by("-uploaded_at")
        self.fields["starter"].queryset = qs
        self.fields["starter"].label_from_instance = lambda u: u.filename
        if qs.exists():
            self.fields["starter"].initial = qs.first().pk

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
    """One choice per finish on the drawing: a Callidus product, or leave out."""

    LEAVE_OUT = ""

    def __init__(self, *args, summary: dict, floors: dict, skirtings: dict, **kwargs):
        super().__init__(*args, **kwargs)
        materials = summary["materials"]
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
