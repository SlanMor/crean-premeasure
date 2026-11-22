"""Platform tests. The full flow needs the Cashel fixtures (PREMEASURE_FIXTURES)."""

import io
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from unittest import skipUnless

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import BackgroundTemplate, Premeasure, Product, Project, Upload
from .tasks import safe_name

FIXTURES = os.environ.get("PREMEASURE_FIXTURES")
CASHEL = Path(FIXTURES) / "cashel" if FIXTURES else None


class PagesTest(TestCase):
    def test_pages_need_sign_in(self):
        r = self.client.get(reverse("home"))
        self.assertRedirects(r, f"{reverse('login')}?next=/")
        self.assertEqual(self.client.get(reverse("health")).json(), {"status": "ok"})

    def test_add_project(self):
        self.client.force_login(User.objects.create_user("est"))
        r = self.client.post(reverse("home"), {"code": "C123", "name": "Cashel school"})
        p = Project.objects.get(code="C123")
        self.assertRedirects(r, reverse("setup", args=[p.pk]))  # new projects start with their products
        self.assertContains(self.client.get(r.url), "Project setup")

    def test_setup_saves_typed_products(self):
        self.client.force_login(User.objects.create_user("est"))
        p = Project.objects.create(code="C1", name="x")
        data = {"p-TOTAL_FORMS": "2", "p-INITIAL_FORMS": "0",
                "p-0-finish_code": "F1-A", "p-0-kind": "floor", "p-0-name": "F1A - Veneto Copper 634",
                "p-0-fill": "#c08550", "p-0-settings_from": "",
                "p-1-finish_code": "", "p-1-kind": "floor", "p-1-name": "", "p-1-fill": "#c8c8c8", "p-1-settings_from": ""}
        r = self.client.post(reverse("setup", args=[p.pk]), data)
        self.assertRedirects(r, reverse("setup", args=[p.pk]))
        (prod,) = p.products.all()
        self.assertEqual((prod.finish_code, prod.rgb), ("F1-A", (0xC0, 0x85, 0x50)))

    def test_codes_match_however_they_are_written(self):
        from .setup import default_mapping

        p = Project.objects.create(code="C1", name="x")
        Product.objects.create(project=p, finish_code="F1A", name="F1A - Veneto Copper 634")
        Product.objects.create(project=p, finish_code="M50-150 (2)", name="Sparrow")
        Product.objects.create(project=p, name="Spare product")
        summary = {"finishes": [
            {"code": "F1-A", "kind": "floor", "suggestion": None},
            {"code": "M50-150", "kind": "floor", "suggestion": "Sparrow"},  # Sparrow belongs to (2)
            {"code": "M50-150 (2)", "kind": "floor", "suggestion": None},
            {"code": "F9", "kind": "floor", "suggestion": "Spare product"},
        ]}
        floors, _ = default_mapping(p, summary)
        self.assertEqual(floors, {"F1-A": "F1A - Veneto Copper 634", "M50-150 (2)": "Sparrow", "F9": "Spare product"})

    def test_rejects_a_file_that_is_not_callidus(self):
        self.client.force_login(User.objects.create_user("est"))
        p = Project.objects.create(code="C1", name="x")
        r = self.client.post(
            reverse("add_starter", args=[p.pk]),
            {"est": SimpleUploadedFile("job.est", b"not a callidus file")},
            follow=True,
        )
        self.assertContains(r, "No products found in this file")
        self.assertFalse(p.uploads.exists())

    def test_safe_file_names(self):
        self.assertEqual(safe_name('C1 GF: "Block A" <rev 2>'), "C1 GF Block A rev 2")
        self.assertEqual(safe_name("..."), "premeasure")


@skipUnless(CASHEL and CASHEL.exists(), "PREMEASURE_FIXTURES not set")
class FlowTest(TestCase):
    """Set up products, read a drawing, write, download: as an estimator would."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.media = tempfile.mkdtemp()
        cls.settings = override_settings(MEDIA_ROOT=cls.media, JOBS_INLINE=True)
        cls.settings.enable()

    @classmethod
    def tearDownClass(cls):
        cls.settings.disable()
        shutil.rmtree(cls.media, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.client.force_login(User.objects.create_user("est"))
        self.est = sorted(CASHEL.glob("*.est"))[0]
        self.cb = sorted(CASHEL.glob("*.CalBack"))[0]
        BackgroundTemplate.objects.create(file=self.upload(self.cb, "house.CalBack"), est=self.upload(self.est, "house.est"))

    def upload(self, path: Path, name: str | None = None) -> SimpleUploadedFile:
        return SimpleUploadedFile(name or path.name, path.read_bytes())

    def read_drawing(self, project) -> Premeasure:
        r = self.client.post(
            reverse("new_premeasure", args=[project.pk]),
            {"drawing": self.upload(CASHEL / "finishes-P2.pdf"), "page": 1, "level": "GF and FF", "revision": "P2"},
        )
        pm = Premeasure.objects.get(project=project)
        self.assertRedirects(r, reverse("premeasure", args=[pm.pk]))
        self.assertEqual(pm.status, Premeasure.Status.REVIEW, pm.error)
        return pm

    def write(self, pm, extra=None) -> zipfile.ZipFile:
        page = self.client.get(reverse("premeasure", args=[pm.pk]))
        form = page.context["form"]
        data = {name: form[name].value() or "" for name in form.fields}
        data.update(extra or {})
        self.client.post(reverse("write", args=[pm.pk]), data)
        pm.refresh_from_db()
        self.assertEqual(pm.status, Premeasure.Status.DONE, pm.error)
        body = b"".join(self.client.get(reverse("download", args=[pm.pk])).streaming_content)
        return zipfile.ZipFile(io.BytesIO(body))

    def read_job(self, z: zipfile.ZipFile, pm):
        from premeasure_engine.callidus import read_est

        out = Path(self.media) / "check"
        z.extractall(out)
        return read_est(next(out.rglob(f"{pm.title}.est")))

    def test_products_from_the_legend_are_added_to_callidus(self):
        project = Project.objects.create(code="CASHEL", name="Cashel school")
        pm = self.read_drawing(project)
        page = self.client.get(reverse("premeasure", args=[pm.pk]))
        self.assertContains(page, "No products are set up for this project yet")
        self.assertContains(page, "Measure by hand: FF5")

        r = self.client.post(reverse("fill_setup", args=[project.pk, pm.pk]), {"next": reverse("premeasure", args=[pm.pk])})
        self.assertRedirects(r, f"{reverse('setup', args=[project.pk])}?next={reverse('premeasure', args=[pm.pk])}")
        ff7 = project.products.get(finish_code="FF7")
        self.assertIn("BLUE ICE 3753", ff7.name)
        self.assertTrue(ff7.settings_from.startswith("FF"))  # a sheet product in the house template
        self.assertEqual(project.products.get(finish_code="SK1").kind, Product.Kind.SKIRTING)

        # save the setup page as a browser would, renaming one product
        fs = self.client.get(reverse("setup", args=[project.pk])).context["formset"]
        data = {f"p-{k}": v for k, v in fs.management_form.initial.items()}
        for f in fs.forms:
            for name, field in f.fields.items():
                value = f[name].value()
                if value not in (None, False):
                    data[f.add_prefix(name)] = value
        i = next(i for i, f in enumerate(fs.forms) if f.instance.finish_code == "FF7")
        data[f"p-{i}-name"] = "P2 FF7 - Marmoleum Concrete Blue Ice 3753"
        r = self.client.post(reverse("setup", args=[project.pk]), data)
        self.assertEqual(r.status_code, 302, r.context and r.context["formset"].errors)
        ff7.refresh_from_db()
        self.assertEqual(ff7.name, "P2 FF7 - Marmoleum Concrete Blue Ice 3753")

        # leave FF1 out; everything else is written, with new products added to the job
        z = self.write(pm, {"floor:FF1": ""})
        names = sorted(Path(n).name for n in z.namelist())
        self.assertEqual(names, sorted([f"{pm.title}.est", f"{pm.title}.est.CalBack",
                                        f"{pm.title} - review.csv", "READ ME FIRST.txt"]))
        job = self.read_job(z, pm)
        self.assertIn(ff7.name, {m.name for m in job.materials})
        self.assertIn(ff7.name, pm.written["new_products"])
        self.assertNotIn("FF1", pm.finish_products)
        self.assertTrue(any(r.material.name == ff7.name for r in job.rooms))

    def test_products_imported_from_a_callidus_job_are_used_as_they_are(self):
        project = Project.objects.create(code="CASHEL", name="Cashel school")
        pm = self.read_drawing(project)
        self.client.post(reverse("add_starter", args=[project.pk]),
                         {"est": self.upload(self.est, "Cashel.est"), "calback": self.upload(self.cb, "Cashel.est.CalBack")})
        # Neptune is the drawing's FF13 but Crean's "FF8 - ...": matched on its product number, not its code
        neptune = project.products.get(name="FF8 - Forbo Concrete Neptune 3756")
        self.assertEqual(neptune.finish_code, "FF13")
        self.assertFalse(project.products.filter(finish_code="FF8", name__startswith="FF8 -").exists())

        # the five P2 finishes Crean's job lacks are added from the legend
        self.client.post(reverse("fill_setup", args=[project.pk, pm.pk]))
        z = self.write(pm)
        self.assertEqual(len(pm.written["new_products"]), 5 + 3, pm.written["new_products"])  # FF7, FF8, FF9, FF11, FF14, 3 skirtings
        job = self.read_job(z, pm)
        self.assertEqual(sum(r.material.name == neptune.name for r in job.rooms), 2)

    def test_write_refuses_a_product_not_in_the_setup(self):
        project = Project.objects.create(code="C", name="x")
        drawing = Upload.objects.create(project=project, kind=Upload.Kind.DRAWING, file=self.upload(CASHEL / "finishes-P2.pdf"))
        pm = Premeasure.objects.create(project=project, title="t", drawing=drawing, status=Premeasure.Status.REVIEW,
                                       summary={"materials": [], "finishes": [
                                           {"code": "FF1", "kind": "floor", "description": "", "suggestion": None}], "rooms": []})
        r = self.client.post(reverse("write", args=[pm.pk]), {"floor:FF1": "Made up"}, follow=True)
        self.assertContains(r, "no longer in the project setup")
        pm.refresh_from_db()
        self.assertEqual(pm.status, Premeasure.Status.REVIEW)
