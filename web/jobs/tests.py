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

from .models import Premeasure, Product, Project, Upload
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
        self.assertRedirects(r, reverse("project", args=[p.pk]))
        self.assertContains(self.client.get(r.url), "Add Callidus job")

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
    """Upload, read, choose products, write, download: as an estimator would."""

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

    def upload(self, path: Path, name: str | None = None) -> SimpleUploadedFile:
        return SimpleUploadedFile(name or path.name, path.read_bytes())

    def test_whole_flow(self):
        self.client.force_login(User.objects.create_user("est"))
        project = Project.objects.create(code="CASHEL", name="Cashel school")
        est = sorted(CASHEL.glob("*.est"))[0]
        cb = sorted(CASHEL.glob("*.CalBack"))[0]
        r = self.client.post(
            reverse("add_starter", args=[project.pk]),
            {"est": self.upload(est, "Cashel.est"), "calback": self.upload(cb, "Cashel.est.CalBack")},
            follow=True,
        )
        self.assertContains(r, "products.")
        starter = project.uploads.get(kind=Upload.Kind.CALLIDUS_EST)
        self.assertIsNotNone(starter.background)

        # read the drawing (inline here; the worker does this on the server)
        r = self.client.post(
            reverse("new_premeasure", args=[project.pk]),
            {"drawing": self.upload(CASHEL / "finishes-P2.pdf"), "page": 1, "level": "GF and FF",
             "starter": starter.pk, "revision": "P2"},
        )
        pm = Premeasure.objects.get()
        self.assertRedirects(r, reverse("premeasure", args=[pm.pk]))
        self.assertEqual(pm.status, Premeasure.Status.REVIEW, pm.error)
        self.assertEqual(pm.finish_products["FF13"], "FF8 - Forbo Concrete Neptune 3756")
        self.assertNotIn("FF7", pm.finish_products)  # not in Crean's file: never guessed
        page = self.client.get(r.url)
        self.assertContains(page, "Measure by hand: FF5")
        self.assertContains(page, "Write Callidus files")
        self.assertEqual(self.client.get(reverse("preview", args=[pm.pk]))["Content-Type"], "image/png")

        # leave FF1 out, put SK1 on the edges, write
        form = {f"floor:{c}": m for c, m in pm.finish_products.items() if c != "FF1"}
        sk1 = pm.summary["materials"][0]
        form["skirting:SK1"] = sk1
        r = self.client.post(reverse("write", args=[pm.pk]), form)
        pm.refresh_from_db()
        self.assertEqual(pm.status, Premeasure.Status.DONE, pm.error)
        self.assertNotIn("FF1", pm.finish_products)
        self.assertEqual(pm.skirting_products, {"SK1": sk1})
        self.assertContains(self.client.get(r.url), "Download Callidus files")

        z = zipfile.ZipFile(io.BytesIO(b"".join(self.client.get(reverse("download", args=[pm.pk])).streaming_content)))
        names = sorted(Path(n).name for n in z.namelist())
        self.assertEqual(names, sorted([f"{pm.title}.est", f"{pm.title}.est.CalBack",
                                        f"{pm.title} - review.csv", "READ ME FIRST.txt"]))

        # choices are kept for the project: FF13's product, and FF1 left out
        self.assertEqual(project.products.get(finish_code="FF13").callidus_material, "FF8 - Forbo Concrete Neptune 3756")
        self.assertEqual(project.products.get(finish_code="FF1").callidus_material, "")
        self.assertEqual(project.products.get(finish_code="SK1").callidus_material, sk1)

        # a second drawing on the project starts from them
        from .tasks import _default_products

        floors, skirtings = _default_products(pm, pm.summary)
        self.assertNotIn("FF1", floors)
        self.assertEqual(skirtings, {"SK1": sk1})
        self.assertEqual(Product.objects.count(), project.products.count())

    def test_write_refuses_a_product_not_in_the_job(self):
        self.client.force_login(User.objects.create_user("est"))
        project = Project.objects.create(code="C", name="x")
        est = Upload.objects.create(project=project, kind=Upload.Kind.CALLIDUS_EST,
                                    file=self.upload(sorted(CASHEL.glob("*.est"))[0]))
        drawing = Upload.objects.create(project=project, kind=Upload.Kind.DRAWING, file=self.upload(CASHEL / "finishes-P2.pdf"))
        pm = Premeasure.objects.create(project=project, title="t", drawing=drawing, starter=est,
                                       status=Premeasure.Status.REVIEW,
                                       summary={"materials": ["A"], "finishes": [
                                           {"code": "FF1", "kind": "floor", "description": "", "suggestion": None}], "rooms": []})
        r = self.client.post(reverse("write", args=[pm.pk]), {"floor:FF1": "Made up"}, follow=True)
        self.assertContains(r, "not in this Callidus job")
        pm.refresh_from_db()
        self.assertEqual(pm.status, Premeasure.Status.REVIEW)
