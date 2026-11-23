"""Platform tests. The full flow needs the Cashel fixtures (PREMEASURE_FIXTURES)."""

import csv
import io
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from unittest import skipUnless

from django.contrib.auth.models import User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import BackgroundTemplate, Batch, LibraryProduct, MeasuringRules, Premeasure, Product, Project, Upload
from .tasks import safe_name

FIXTURES = os.environ.get("PREMEASURE_FIXTURES")
CASHEL = Path(FIXTURES) / "cashel" if FIXTURES else None


class PagesTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("est", email="est@crean.ie")

    def test_pages_need_sign_in(self):
        r = self.client.get(reverse("home"))
        self.assertRedirects(r, f"{reverse('login')}?next=/")
        self.assertEqual(self.client.get(reverse("health")).json(), {"status": "ok"})

    def test_settings_are_for_staff_only(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("settings")).status_code, 302)
        self.assertNotContains(self.client.get(reverse("home")), ">Settings<")
        self.user.is_staff = True
        self.user.save()
        self.assertContains(self.client.get(reverse("settings")), "Callidus template")
        self.assertContains(self.client.get(reverse("home")), ">Settings<")

    def test_add_a_user_from_settings(self):
        self.user.is_staff = True
        self.user.save()
        self.client.force_login(self.user)
        r = self.client.post(reverse("settings_users"), {
            "username": "patrick", "first_name": "Patrick", "last_name": "Walsh", "email": "p@crean.ie",
            "password": "a-long-password-1", "is_active": "on",
        })
        self.assertRedirects(r, reverse("settings_users"))
        u = User.objects.get(username="patrick")
        self.assertTrue(u.check_password("a-long-password-1"))
        self.assertFalse(u.is_staff)

    def test_cannot_lock_yourself_out(self):
        self.user.is_staff = True
        self.user.save()
        self.client.force_login(self.user)
        r = self.client.post(reverse("settings_user", args=[self.user.pk]),
                             {"username": "est", "email": "est@crean.ie", "is_active": "on"})
        self.assertContains(r, "cannot remove your own access")

    def test_new_tender_starts_the_set_up(self):
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse("home")), "New tender")
        r = self.client.post(reverse("new_tender"), {"code": "C123", "name": "Cashel school"})
        p = Project.objects.get(code="C123")
        self.assertRedirects(r, reverse("wizard", args=[p.pk, "rules"]))
        self.assertContains(self.client.get(r.url), "Tender questions")

    def test_new_product_from_a_setup_row_is_added_to_the_list_and_chosen(self):
        self.client.force_login(self.user)
        p = Project.objects.create(code="C1", name="x")
        row = Product.objects.create(project=p, finish_code="F1-A", description="Marmoleum sheet")
        nxt = reverse("setup", args=[p.pk])
        r = self.client.post(f"{reverse('product_new')}?row={row.pk}&next={nxt}", {
            "name": "F1A - Veneto Copper 634", "type": "sheet", "fill": "#c08550", "wastage_pct": "5",
            "active": "on", "callidus_settings_from": "",
        })
        self.assertRedirects(r, nxt)
        row.refresh_from_db()
        self.assertEqual(row.library.name, "F1A - Veneto Copper 634")
        self.assertEqual(row.library.rgb, (0xC0, 0x85, 0x50))

    def test_setup_rows_choose_from_the_list(self):
        self.client.force_login(self.user)
        p = Project.objects.create(code="C1", name="x")
        lib = LibraryProduct.objects.create(name="F1A - Veneto Copper 634")
        data = {"p-TOTAL_FORMS": "2", "p-INITIAL_FORMS": "0",
                "p-0-finish_code": "F1-A", "p-0-library": str(lib.pk), "p-1-finish_code": "", "p-1-library": ""}
        r = self.client.post(reverse("setup", args=[p.pk]), data)
        self.assertRedirects(r, reverse("setup", args=[p.pk]))
        self.assertEqual(p.products.get().library, lib)

    def test_codes_match_however_they_are_written(self):
        from .setup import default_mapping

        p = Project.objects.create(code="C1", name="x")
        copper = LibraryProduct.objects.create(name="F1A - Veneto Copper 634")
        sparrow = LibraryProduct.objects.create(name="Sparrow")
        coving = LibraryProduct.objects.create(name="Coving", type="skirting")
        Product.objects.create(project=p, finish_code="F1A", library=copper)
        Product.objects.create(project=p, finish_code="M50-150 (2)", library=sparrow)
        Product.objects.create(project=p, finish_code="SK1", library=coving, kind="skirting")
        Product.objects.create(project=p, finish_code="F9")  # no product chosen yet
        summary = {"finishes": [
            {"code": "F1-A", "kind": "floor"}, {"code": "M50-150", "kind": "floor"},
            {"code": "M50-150 (2)", "kind": "floor"}, {"code": "F9", "kind": "floor"},
            {"code": "SK1", "kind": "skirting"},
        ]}
        floors, skirtings = default_mapping(p, summary)
        self.assertEqual(floors, {"F1-A": copper.name, "M50-150 (2)": "Sparrow"})
        self.assertEqual(skirtings, {"SK1": "Coving"})

    def test_rules_follow_the_company_until_a_project_sets_its_own(self):
        self.client.force_login(self.user)
        p = Project.objects.create(code="C1", name="x")
        company = MeasuringRules.company()
        company.min_opening_m2 = 1
        company.save()
        self.assertEqual(MeasuringRules.for_project(p).min_opening_m2, 1)
        form = self.client.get(reverse("project_rules", args=[p.pk])).context["form"]
        data = {n: ("" if form[n].value() is None else form[n].value()) for n in form.fields}
        data["min_opening_m2"] = "0.5"
        self.client.post(reverse("project_rules", args=[p.pk]), data)
        self.assertEqual(float(MeasuringRules.for_project(p).min_opening_m2), 0.5)
        self.assertEqual(float(MeasuringRules.company().min_opening_m2), 1)
        self.client.post(reverse("project_rules", args=[p.pk]), {"action": "use_company"})
        self.assertEqual(float(MeasuringRules.for_project(p).min_opening_m2), 1)

    def test_safe_file_names(self):
        self.assertEqual(safe_name('C1 GF: "Block A" <rev 2>'), "C1 GF Block A rev 2")
        self.assertEqual(safe_name("..."), "premeasure")


@skipUnless(CASHEL and CASHEL.exists(), "PREMEASURE_FIXTURES not set")
@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", EMAIL_HOST="smtp.test")
class FlowTest(TestCase):
    """Set up products, read drawings, write, download: as an estimator would."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.media = tempfile.mkdtemp()
        cls.media_settings = override_settings(MEDIA_ROOT=cls.media, JOBS_INLINE=True)
        cls.media_settings.enable()

    @classmethod
    def tearDownClass(cls):
        cls.media_settings.disable()
        shutil.rmtree(cls.media, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.user = User.objects.create_user("est", email="est@crean.ie", first_name="Pat")
        self.client.force_login(self.user)
        self.est = sorted(CASHEL.glob("*.est"))[0]
        self.cb = sorted(CASHEL.glob("*.CalBack"))[0]
        BackgroundTemplate.objects.create(file=self.upload(self.cb, "house.CalBack"), est=self.upload(self.est, "house.est"))
        self.project = Project.objects.create(code="CASHEL", name="Cashel school")

    def upload(self, path: Path, name: str | None = None) -> SimpleUploadedFile:
        return SimpleUploadedFile(name or path.name, path.read_bytes())

    def add_drawings(self, files, **extra):
        data = {"drawings": files, "level": "", "revision": "P2", "auto_write": "on", "notify": "on"}
        data.update(extra)
        data = {k: v for k, v in data.items() if v != ""}
        return self.client.post(reverse("new_premeasure", args=[self.project.pk]), data)

    def zip_of(self, pm) -> zipfile.ZipFile:
        body = b"".join(self.client.get(reverse("download", args=[pm.pk])).streaming_content)
        return zipfile.ZipFile(io.BytesIO(body))

    def read_job(self, z, pm):
        from premeasure_engine.callidus import read_est

        out = Path(tempfile.mkdtemp(dir=self.media))
        z.extractall(out)
        return read_est(next(out.rglob(f"{pm.title}.est")))

    def test_new_products_are_added_to_callidus_and_quantities_carry_wastage(self):
        r = self.add_drawings([self.upload(CASHEL / "finishes-P2.pdf")], auto_write="")
        pm = Premeasure.objects.get()
        self.assertRedirects(r, reverse("premeasure", args=[pm.pk]))
        self.assertEqual(pm.status, Premeasure.Status.REVIEW, pm.error)
        page = self.client.get(r.url)
        self.assertContains(page, "no product in the project yet")
        self.assertContains(page, "Measure by hand: FF5")

        self.client.post(reverse("fill_setup", args=[self.project.pk, pm.pk]))
        ff7 = self.project.products.get(finish_code="FF7")
        self.assertIsNone(ff7.library)  # nothing in the (empty) list matches
        self.assertEqual(self.project.products.get(finish_code="SK1").kind, "skirting")

        # a new product for FF7, made from its setup row
        self.client.post(f"{reverse('product_new')}?row={ff7.pk}", {
            "name": "P2 FF7 - Marmoleum Concrete Blue Ice 3753", "type": "sheet", "fill": "#8caac8",
            "wastage_pct": "7.5", "active": "on", "callidus_settings_from": ""})
        ff7.refresh_from_db()
        self.assertIsNotNone(ff7.library)

        page = self.client.get(reverse("premeasure", args=[pm.pk]))
        form = page.context["form"]
        data = {n: form[n].value() or "" for n in form.fields}
        self.assertEqual(data["floor:FF7"], ff7.library.name)
        self.client.post(reverse("write", args=[pm.pk]), data)
        pm.refresh_from_db()
        self.assertEqual(pm.status, Premeasure.Status.DONE, pm.error)
        self.assertEqual(pm.written["new_products"], [ff7.library.name])

        z = self.zip_of(pm)
        self.assertEqual(sorted(Path(n).name for n in z.namelist()), sorted([
            f"{pm.title}.est", f"{pm.title}.est.CalBack", f"{pm.title} - review.csv",
            f"{pm.title} - quantities.csv", "READ ME FIRST.txt"]))
        q = next(n for n in z.namelist() if n.endswith("quantities.csv"))
        rows = list(csv.DictReader(io.StringIO(z.read(q).decode("utf-8-sig"))))
        (row,) = [r for r in rows if r["Product"] == ff7.library.name]
        self.assertAlmostEqual(float(row["Quantity with wastage"]), float(row["Net quantity"]) * 1.075, places=1)
        job = self.read_job(z, pm)
        self.assertTrue(any(r.material.name == ff7.library.name for r in job.rooms))

    def test_finishes_schedule_settles_shared_colours_and_is_shown(self):
        sched = CASHEL / "finishes-schedule.pdf"
        if not (CASHEL / "finishes-P3.pdf").exists() or not sched.exists():
            self.skipTest("Cashel P3 and its schedule are not in the fixtures")
        self.add_drawings([self.upload(CASHEL / "finishes-P3.pdf")], revision="P3", auto_write="", notify="")
        pm = Premeasure.objects.get()
        self.assertIn("FF7 or FF11", pm.summary["shared_colours"])
        self.assertContains(self.client.get(reverse("premeasure", args=[pm.pk])), "settles them by room number")

        bad = self.client.post(reverse("project_schedule", args=[self.project.pk]),
                               {"schedule": SimpleUploadedFile("schedule.xlsx", b"not a pdf"), "reread": "on"})
        self.assertRedirects(bad, reverse("project", args=[self.project.pk]))
        self.assertIsNone(self.project.finishes_schedule())

        self.client.post(reverse("project_schedule", args=[self.project.pk]),
                         {"schedule": self.upload(sched, "Finish schedule.pdf"), "reread": "on"})
        pm.refresh_from_db()
        s = pm.summary["schedule"]
        self.assertEqual(s["rows"], 12)
        self.assertGreater(s["settled_m2"], 680)
        self.assertEqual({d["room"] for d in s["differs"]}, {"F16", "G24"})
        codes = {f["code"] for f in pm.summary["finishes"] if f["rooms"]}
        self.assertFalse({"FF7 or FF11", "FF8 or FF10"} & codes)
        page = self.client.get(reverse("premeasure", args=[pm.pk]))
        self.assertContains(page, "Finishes schedule")
        self.assertContains(page, "F16, G24")
        self.assertContains(self.client.get(reverse("project", args=[self.project.pk])), "Finish_schedule.pdf")

        self.client.post(reverse("project_schedule", args=[self.project.pk]), {"action": "remove"})
        self.assertIsNone(self.project.finishes_schedule())

    def test_import_a_callidus_job_then_bulk_upload_runs_to_the_end(self):
        # read one drawing so the import can match legend finishes to Crean's products
        self.add_drawings([self.upload(CASHEL / "finishes-P2.pdf")], auto_write="", notify="")
        first = Premeasure.objects.get()
        self.client.post(reverse("add_starter", args=[self.project.pk]),
                         {"est": self.upload(self.est, "Cashel.est"), "calback": self.upload(self.cb, "Cashel.est.CalBack")})
        neptune = LibraryProduct.objects.get(name="FF8 - Forbo Concrete Neptune 3756")
        self.assertEqual(self.project.products.get(library=neptune).finish_code, "FF13")  # by product number
        self.assertEqual(LibraryProduct.objects.get(name="125mm coved skirting").type, "skirting")

        # the five P2 finishes Crean's job lacks, and softwood skirting: new products
        self.client.post(reverse("fill_setup", args=[self.project.pk, first.pk]))
        for row in self.project.products.filter(library=None):
            kind = "skirting" if row.kind == "skirting" else "sheet"
            row.library = LibraryProduct.objects.create(name=f"{row.finish_code} - {row.description[:60]}", type=kind)
            row.save()

        # bulk: a two-page PDF (only one page is a plan) and the plan again, left to run
        import pypdfium2 as pdfium

        doc = pdfium.PdfDocument.new()
        src = pdfium.PdfDocument(str(CASHEL / "finishes-P2.pdf"))
        doc.import_pages(src)
        doc.new_page(595, 842)  # a blank page: not a plan
        buf = io.BytesIO()
        doc.save(buf)
        r = self.add_drawings([SimpleUploadedFile("two-pages.pdf", buf.getvalue()),
                               self.upload(CASHEL / "finishes-P2.pdf", "again.pdf")])
        b = Batch.objects.latest("created_at")
        self.assertRedirects(r, reverse("batch", args=[b.pk]))
        drawings = list(b.premeasures.all())
        self.assertEqual(len(drawings), 2)  # the blank page was skipped
        for d in drawings:
            self.assertEqual(d.status, Premeasure.Status.DONE, d.error)  # every finish had a product
        b.refresh_from_db()
        self.assertIsNotNone(b.notified_at)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("2 ready in Callidus", mail.outbox[0].subject)
        self.assertContains(self.client.get(reverse("batch", args=[b.pk])), "Callidus files ready")
        job = self.read_job(self.zip_of(drawings[0]), drawings[0])
        self.assertEqual(sum(r.material.name == neptune.name for r in job.rooms), 2)

    def test_write_refuses_a_product_not_in_the_list(self):
        drawing = Upload.objects.create(project=self.project, kind=Upload.Kind.DRAWING,
                                        file=self.upload(CASHEL / "finishes-P2.pdf"))
        pm = Premeasure.objects.create(project=self.project, title="t", drawing=drawing, status=Premeasure.Status.REVIEW,
                                       summary={"materials": [], "finishes": [
                                           {"code": "FF1", "kind": "floor", "description": "", "amount": 1}], "rooms": []})
        r = self.client.post(reverse("write", args=[pm.pk]), {"floor:FF1": "Made up"}, follow=True)
        self.assertContains(r, "no longer in the product list")
        pm.refresh_from_db()
        self.assertEqual(pm.status, Premeasure.Status.REVIEW)


@skipUnless(CASHEL and CASHEL.exists(), "PREMEASURE_FIXTURES not set")
class ScaleTest(TestCase):
    def test_a_missing_scale_can_be_given_and_the_drawing_read_again(self):
        media = tempfile.mkdtemp()
        with override_settings(MEDIA_ROOT=media, JOBS_INLINE=True):
            self.client.force_login(User.objects.create_user("est"))
            project = Project.objects.create(code="C", name="x")
            drawing = Upload.objects.create(project=project, kind=Upload.Kind.DRAWING,
                                            file=SimpleUploadedFile("p2.pdf", (CASHEL / "finishes-P2.pdf").read_bytes()))
            pm = Premeasure.objects.create(project=project, title="t", drawing=drawing,
                                           status=Premeasure.Status.FAILED,
                                           error="No drawing scale (such as 1:100) is printed on this sheet.")
            self.assertTrue(pm.needs_scale)
            self.assertContains(self.client.get(reverse("premeasure", args=[pm.pk])), "What scale is this drawing?")
            self.client.post(reverse("set_scale", args=[pm.pk]), {"scale": "100"})
            pm.refresh_from_db()
            self.assertEqual((pm.scale, pm.status, pm.summary["scale"]), (100, Premeasure.Status.REVIEW, 100.0))
        shutil.rmtree(media, ignore_errors=True)


class PictureTest(TestCase):
    def test_a_scanned_drawing_asks_for_its_sheet_size_and_scale(self):
        import io

        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (1654, 1169), "white").save(buf, "PDF", resolution=100)  # a blank "scan"
        media = tempfile.mkdtemp()
        with override_settings(MEDIA_ROOT=media, JOBS_INLINE=True):
            self.client.force_login(User.objects.create_user("est"))
            project = Project.objects.create(code="C", name="x")
            drawing = Upload.objects.create(project=project, kind=Upload.Kind.DRAWING,
                                            file=SimpleUploadedFile("scan.pdf", buf.getvalue()))
            pm = Premeasure.objects.create(project=project, title="t", drawing=drawing)
            from .tasks import analyse_premeasure

            analyse_premeasure(pm.pk)
            pm.refresh_from_db()
            self.assertEqual(pm.status, Premeasure.Status.FAILED)
            self.assertTrue(pm.needs_sheet and pm.needs_scale and pm.plain_error)
            page = self.client.get(reverse("premeasure", args=[pm.pk]))
            self.assertContains(page, "What sheet size and scale is this drawing?")
            self.assertContains(page, '<option value="A0"')
            self.client.post(reverse("set_scale", args=[pm.pk]), {"scale": "100", "sheet": "A3"})
            pm.refresh_from_db()
            self.assertEqual((pm.sheet, pm.scale), ("A3", 100))
            # read again with both: a blank sheet has no legend, said in plain words
            self.assertEqual(pm.status, Premeasure.Status.FAILED)
            self.assertIn("no finish legend", pm.error)
            self.assertTrue(pm.plain_error and not pm.needs_scale)
        shutil.rmtree(media, ignore_errors=True)


class DrawingProductsTest(TestCase):
    """A drawing follows the project's products as they are now, even after it
    has been written, except where a different product was chosen on it."""

    def test_products_added_after_writing_show_on_the_drawing(self):
        media = tempfile.mkdtemp()
        with override_settings(MEDIA_ROOT=media):
            self.client.force_login(User.objects.create_user("est"))
            carpet = LibraryProduct.objects.create(name="Carpet A")
            LibraryProduct.objects.create(name="Vinyl B")
            LibraryProduct.objects.create(name="Lino C")
            project = Project.objects.create(code="P", name="p")
            finishes = [{"code": c, "kind": "floor", "description": "", "amount": 10.0, "fill": [1, 2, 3],
                         "in_legend": True, "custom_code": False, "rooms": 1, "suggestion": "", "why": ""}
                        for c in ("FF1", "FF2", "FF3")]
            drawing = Upload.objects.create(project=project, kind=Upload.Kind.DRAWING,
                                            file=SimpleUploadedFile("x.pdf", b"%PDF"))
            pm = Premeasure.objects.create(
                project=project, title="t", drawing=drawing, status=Premeasure.Status.DONE,
                summary={"scale": 100, "finishes": finishes, "rooms": [], "unknown_fills": [], "manual_finishes": []},
                finish_products={"FF1": "Vinyl B"}, output="outputs/t.zip",  # written before the setup had products
            )
            Product.objects.create(project=project, finish_code="FF1", library=carpet, kind="floor")
            Product.objects.create(project=project, finish_code="FF2", library=carpet, kind="floor")

            def shown():
                form = self.client.get(reverse("premeasure", args=[pm.pk])).context["form"]
                return {k: form[k].value() for k in form.fields}

            # the project's products now cover FF1 and FF2, so they win over the older write
            self.assertEqual(shown(), {"floor:FF1": "Carpet A", "floor:FF2": "Carpet A", "floor:FF3": ""})
            self.assertContains(self.client.get(reverse("premeasure", args=[pm.pk])), "The products have changed")
            # writing again keeps only what differs from the project's products
            from unittest import mock

            with mock.patch("jobs.views.enqueue"):
                self.client.post(reverse("write", args=[pm.pk]),
                                 {"floor:FF1": "Carpet A", "floor:FF2": "Carpet A", "floor:FF3": "Lino C"})
            pm.refresh_from_db()
            # FF3 had no product in the project: it is added there, not kept as this drawing's own
            self.assertEqual(pm.chosen_here, {"floor": {}, "skirting": {}})
            self.assertEqual(project.products.get(finish_code="FF3").library.name, "Lino C")
            pm.status = Premeasure.Status.DONE  # as when the write has finished
            pm.save()
            # a later change to the project's products reaches the drawing
            Product.objects.filter(finish_code="FF2").update(library=LibraryProduct.objects.get(name="Vinyl B"))
            self.assertEqual(shown(), {"floor:FF1": "Carpet A", "floor:FF2": "Vinyl B", "floor:FF3": "Lino C"})
        shutil.rmtree(media, ignore_errors=True)


class ProductsPageTest(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_user("est"))
        self.project = Project.objects.create(code="P", name="p")
        self.a = LibraryProduct.objects.create(name="Carpet A", type="carpet_tile")
        self.b = LibraryProduct.objects.create(name="Vinyl B")
        self.row = Product.objects.create(project=self.project, finish_code="FF1", kind="floor",
                                          description="CARPET PLANK TILE - Interface HN810 Slate")
        Product.objects.create(project=self.project, finish_code="FF2", kind="floor")

    def post(self, extra, next_url=""):
        page = self.client.get(reverse("setup", args=[self.project.pk]))
        fs = page.context["formset"]
        data = {"p-TOTAL_FORMS": str(len(fs.forms)), "p-INITIAL_FORMS": str(len(fs.initial_forms)),
                "p-MIN_NUM_FORMS": "0", "p-MAX_NUM_FORMS": "1000"}
        for i, f in enumerate(fs.forms):
            if f.instance.pk:
                data.update({f"p-{i}-id": f.instance.pk, f"p-{i}-finish_code": f.instance.finish_code,
                             f"p-{i}-library": self.b.pk if f.instance.finish_code == "FF2" else ""})
        data.update(extra)
        url = reverse("setup", args=[self.project.pk]) + (f"?next={next_url}" if next_url else "")
        return self.client.post(url, data)

    def test_adding_a_new_product_saves_the_page_first(self):
        r = self.post({"new_for": str(self.row.pk)})
        self.assertEqual(self.project.products.get(finish_code="FF2").library, self.b)  # not lost
        self.assertIn(reverse("product_new"), r.url)
        self.assertIn("type=carpet_tile", r.url)
        self.assertIn(f"row={self.row.pk}", r.url)

    def test_save_and_go_back_to_the_drawing(self):
        r = self.post({}, next_url="/premeasure/1/")
        self.assertEqual(r.url, "/premeasure/1/")
        self.assertEqual(self.project.products.get(finish_code="FF2").library, self.b)

    def test_shortlist_from_the_master_list(self):
        self.post({"s-add": [str(self.a.pk)], "stay": "1"})
        self.assertEqual(list(self.project.shortlist.all()), [self.a])
        page = self.client.get(reverse("setup", args=[self.project.pk]))
        self.assertContains(page, "This project&#x27;s products")
        self.assertEqual({p.name for p in self.project.project_products()}, {"Carpet A", "Vinyl B"})
        self.post({"drop": [str(self.a.pk)], "stay": "1"})
        self.assertFalse(self.project.shortlist.exists())
