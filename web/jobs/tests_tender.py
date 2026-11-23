"""The tender set-up, its to-do list, room checks, queries and quantities."""

import io
import os
import shutil
import tempfile
from pathlib import Path
from unittest import mock, skipUnless

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import (BackgroundTemplate, Company, LibraryProduct, MeasuringRules, Premeasure, Product, Project,
                     RoomCheck, Upload)
from .tender import codes_in_tender, queries, rooms_to_check, suggest_from_history, tender_todo, tender_workbook

FIXTURES = os.environ.get("PREMEASURE_FIXTURES")
CASHEL = Path(FIXTURES) / "cashel" if FIXTURES else None


def finish(code, description="", amount=10.0, kind="floor", in_legend=True):
    return {"code": code, "kind": kind, "description": description, "amount": amount, "fill": [200, 100, 50],
            "in_legend": in_legend, "custom_code": False, "rooms": 1, "suggestion": "", "why": ""}


def room(i, code, area, status="Needs review", reasons=("opening of 3 m2",), skirting=None):
    return {"id": i, "label": f"R{i}", "finish": code, "area_m2": area, "skirting_m": skirting or {},
            "status": status, "reasons": list(reasons) if status != "Verified" else [], "at": [0, 0],
            "bounds": [0, 0, 1, 1]}


class TenderBase(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.settings_override = override_settings(MEDIA_ROOT=self.media)
        self.settings_override.enable()
        self.user = User.objects.create_user("est", first_name="Pat")
        self.client.force_login(self.user)

    def tearDown(self):
        self.settings_override.disable()
        shutil.rmtree(self.media, ignore_errors=True)

    def drawing(self, project, title, finishes, rooms=(), status=Premeasure.Status.REVIEW, **summary):
        up = Upload.objects.create(project=project, kind=Upload.Kind.DRAWING, drawing_number=title,
                                   file=SimpleUploadedFile(f"{title}.pdf", b"%PDF"))
        s = {"scale": 100, "finishes": finishes, "rooms": list(rooms), "unknown_fills": [], "manual_finishes": []}
        s.update(summary)
        return Premeasure.objects.create(project=project, title=title, drawing=up, status=status, summary=s)


class CompaniesTest(TenderBase):
    def test_new_tender_picks_or_adds_companies(self):
        bam = Company.objects.create(kind="contractor", name="BAM Ireland")
        r = self.client.post(reverse("new_tender"), {
            "code": "T1", "name": "School", "client": "__new__", "client_new": "Department of Education",
            "main_contractor": str(bam.pk), "architect": "__new__", "architect_new": "RKD",
            "due": "2026-12-18",
        })
        p = Project.objects.get(code="T1")
        self.assertRedirects(r, reverse("wizard", args=[p.pk, "rules"]))
        self.assertEqual((p.client.name, p.client.kind), ("Department of Education", "client"))
        self.assertEqual(p.main_contractor, bam)
        self.assertEqual(p.architect.name, "RKD")
        self.assertEqual(str(p.due), "2026-12-18")
        # the new ones are in the lists next time
        page = self.client.get(reverse("new_tender"))
        self.assertContains(page, "Department of Education")
        self.assertContains(page, "Add a new architect...")

    def test_new_without_a_name_is_refused(self):
        r = self.client.post(reverse("new_tender"), {"code": "T1", "name": "x", "client": "__new__"})
        self.assertContains(r, "Type the name.")
        self.assertFalse(Project.objects.exists())

    def test_settings_list_staff_only(self):
        self.assertEqual(self.client.get(reverse("companies")).status_code, 302)
        self.user.is_staff = True
        self.user.save()
        r = self.client.post(reverse("company_new"), {"kind": "architect", "name": "Henry J Lyons",
                                                      "email": "a@hjl.ie", "active": "on"})
        self.assertEqual(r.status_code, 302)
        self.assertContains(self.client.get(reverse("companies") + "?kind=architect"), "Henry J Lyons")


class WizardTest(TenderBase):
    def test_rules_saved_only_where_they_differ(self):
        p = Project.objects.create(code="T", name="t", setup_step="rules")
        form = self.client.get(reverse("wizard", args=[p.pk, "rules"])).context["form"]
        data = {k: ("" if v is None else v) for k, v in form.initial.items() if k not in ("id", "project")}
        r = self.client.post(reverse("wizard", args=[p.pk, "rules"]), data)
        self.assertRedirects(r, reverse("wizard", args=[p.pk, "drawings"]))
        self.assertFalse(MeasuringRules.objects.filter(project=p).exists())
        p.refresh_from_db()
        self.assertEqual(p.setup_step, "drawings")
        data["min_opening_m2"] = "1.5"
        self.client.post(reverse("wizard", args=[p.pk, "rules"]), data)
        self.assertEqual(float(MeasuringRules.objects.get(project=p).min_opening_m2), 1.5)

    def test_company_rules_hold_creans_answers(self):
        company = MeasuringRules.company()
        self.assertEqual(float(company.min_opening_m2), 0.5)
        self.assertEqual((company.furniture, company.stairs, company.lift_floors),
                         ("kicker_100", "treads_lm", "once"))
        self.assertEqual(float(company.wastage_for("sheet")), 10)
        self.assertEqual(float(company.wastage_for("carpet_tile")), 5)
        self.assertIsNone(company.wastage_for("tile"))  # Crean gave no figure: not set, never 0
        self.assertIn("One Callidus file per floor plan.", company.other_rules)

    def test_a_tenders_blank_wastage_falls_back_to_the_company(self):
        p = Project.objects.create(code="W", name="w")
        own = MeasuringRules.objects.create(project=p, wastage_sheet=12)
        self.assertEqual(float(own.wastage_for("sheet")), 12)
        self.assertEqual(float(own.wastage_for("skirting")), 10)
        self.assertIsNone(own.wastage_for("matwell"))
        tile = LibraryProduct.objects.create(name="Some tile", type="tile")
        sheet = LibraryProduct.objects.create(name="Some sheet", type="sheet")
        from .setup import wastage_for
        self.assertEqual(wastage_for(p, [tile.name, sheet.name, "Only in Callidus"]),
                         {tile.name: None, sheet.name: 12.0, "Only in Callidus": None})

    def test_products_step_collects_codes_and_remembers_earlier_tenders(self):
        rkd = Company.objects.create(kind="architect", name="RKD")
        lino = LibraryProduct.objects.create(name="Forbo Marmoleum Taiga")
        earlier = Project.objects.create(code="OLD", name="old", architect=rkd)
        Product.objects.create(project=earlier, finish_code="FL-401", library=lino,
                               description="Marmoleum Real 3203 Taiga")
        p = Project.objects.create(code="T", name="t", architect=rkd, setup_step="products")
        self.drawing(p, "GF", [finish("FL-401", "Marmoleum Real 3203 Taiga", 120), finish("FL-602", "Carpet A", 40)])
        self.drawing(p, "1F", [finish("FL-401", "Marmoleum Real 3203 Taiga", 80), finish("FL-602", "Carpet B", 10)])
        page = self.client.get(reverse("wizard", args=[p.pk, "products"]))
        self.assertEqual(p.products.get(finish_code="FL-401").library, lino)  # same architect, same legend
        self.assertEqual(p.products.get(finish_code="FL-602").library, None)
        self.assertContains(page, "As on OLD (same architect)")
        self.assertContains(page, "The same code means different things")
        self.assertEqual(codes_in_tender(p)["FL401"].amount, 200)
        # save and go to the last step
        fs = page.context["formset"]
        data = {"p-TOTAL_FORMS": str(len(fs.forms)), "p-INITIAL_FORMS": str(len(fs.initial_forms)),
                "p-MIN_NUM_FORMS": "0", "p-MAX_NUM_FORMS": "1000"}
        for i, f in enumerate(fs.forms):
            if f.instance.pk:
                data.update({f"p-{i}-id": f.instance.pk, f"p-{i}-finish_code": f.instance.finish_code,
                             f"p-{i}-library": f.instance.library_id or ""})
        r = self.client.post(reverse("wizard", args=[p.pk, "products"]), data)
        self.assertRedirects(r, reverse("wizard", args=[p.pk, "done"]))
        with mock.patch("jobs.queue.enqueue"):
            self.client.post(reverse("wizard", args=[p.pk, "done"]))
        p.refresh_from_db()
        self.assertEqual(p.setup_step, "done")


class TodoTest(TenderBase):
    def test_to_do_list_walks_the_tender(self):
        p = Project.objects.create(code="T", name="t")
        vinyl = LibraryProduct.objects.create(name="Vinyl")
        Product.objects.create(project=p, finish_code="FF1", library=vinyl)
        d = self.drawing(p, "GF", [finish("FF1"), finish("FF2")],
                         [room(0, "FF1", 50), room(1, "FF1", 20, status="Verified"), room(2, "FF2", 30)])
        texts = [t.text for t in tender_todo(p)]
        self.assertTrue(any("FF2" in t and "no product" in t for t in texts), texts)
        self.assertTrue(any(t.startswith("1 room to check") for t in texts), texts)  # FF2 has no product yet
        self.assertFalse(any("ready to write" in t for t in texts))
        Product.objects.create(project=p, finish_code="FF2", library=vinyl)
        texts = [t.text for t in tender_todo(p)]
        self.assertTrue(any(t.startswith("2 rooms to check") for t in texts), texts)
        self.assertTrue(any("1 drawing ready to write" in t for t in texts), texts)
        page = self.client.get(reverse("project", args=[p.pk]))
        self.assertContains(page, "Check rooms")
        self.assertContains(page, "Write Callidus files")
        with mock.patch("jobs.queue.enqueue") as q:
            self.client.post(reverse("write_ready", args=[p.pk]))
        d.refresh_from_db()
        self.assertEqual(d.status, Premeasure.Status.WRITING)
        self.assertEqual(d.finish_products, {"FF1": "Vinyl", "FF2": "Vinyl"})
        q.assert_called_once_with("jobs.tasks.write_premeasure", d.pk)


class CheckTest(TenderBase):
    def test_rooms_checked_one_at_a_time_biggest_first(self):
        p = Project.objects.create(code="T", name="t")
        Product.objects.create(project=p, finish_code="FF1", library=LibraryProduct.objects.create(name="Vinyl"))
        d = self.drawing(p, "GF", [finish("FF1")], [room(0, "FF1", 20), room(1, "FF1", 50), room(2, "FF1", 5)])
        page = self.client.get(reverse("check", args=[d.pk]))
        self.assertEqual(page.context["room"]["id"], 1)
        self.assertContains(page, "3 of 3 left")
        self.client.post(reverse("check", args=[d.pk]), {"room": "1", "decision": "ok"})
        self.client.post(reverse("check", args=[d.pk]), {"room": "0", "decision": "by_hand", "note": "runs into the corridor"})
        self.assertEqual([r["id"] for r in rooms_to_check(d)], [2])
        self.assertEqual(RoomCheck.objects.get(room_id=0).note, "runs into the corridor")
        self.client.post(reverse("check", args=[d.pk]), {"room": "1", "decision": "undo"})
        self.assertEqual([r["id"] for r in rooms_to_check(d)], [1, 2])
        drawing_page = self.client.get(reverse("premeasure", args=[d.pk]))
        self.assertContains(drawing_page, "Measure by hand")
        self.assertContains(drawing_page, "Next: check 2 rooms")
        # what was marked wrong is in the quantities as to measure by hand, not priced
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(tender_workbook(p)))
        q = list(wb["Quantities"].iter_rows(min_row=5, values_only=True))
        self.assertEqual(q[0][:5], ("Vinyl", "Sheet (roll)", "m2", 55.0, 55.0))
        hand = list(wb["Measure by hand"].iter_rows(min_row=2, values_only=True))
        self.assertEqual(hand[0][4], 20)
        self.user.is_staff = True
        self.user.save()
        self.assertContains(self.client.get(reverse("feedback")), "runs into the corridor")


class QueriesTest(TenderBase):
    def test_queries_gathered_and_emailed(self):
        rkd = Company.objects.create(kind="architect", name="RKD", contact_name="Aoife Byrne", email="aoife@rkd.ie")
        p = Project.objects.create(code="T", name="UCD Science", architect=rkd)
        self.drawing(p, "GF", [finish("FL-401", "Lino"), finish("FL-999", in_legend=False)],
                     picture={"unclear_codes": ["P11"]}, manual_finishes=["FL-903"],
                     unknown_fills=[{"colour": [216, 216, 176], "area_m2": 450.0}])
        self.drawing(p, "1F", [finish("FL-401", "Carpet")])
        texts = [q.text for q in queries(p)]
        self.assertEqual(len(texts), 6, texts)
        page = self.client.get(reverse("queries", args=[p.pk]))
        self.assertContains(page, "aoife@rkd.ie")
        self.assertContains(page, "Hi Aoife,")
        self.assertContains(page, "FL-401 is described differently")
        self.assertContains(page, "scanned image")


class SuggestTest(TenderBase):
    def test_no_architect_or_client_no_memory(self):
        p = Project.objects.create(code="T", name="t")
        self.assertEqual(suggest_from_history(p), [])


@skipUnless(CASHEL and CASHEL.exists(), "PREMEASURE_FIXTURES not set")
class RealDrawingTest(TenderBase):
    def test_close_up_and_rooms_left_out_when_written(self):
        from .tasks import analyse_premeasure, write_premeasure

        est = sorted(CASHEL.glob("*.est"))[0]
        cb = sorted(CASHEL.glob("*.CalBack"))[0]
        BackgroundTemplate.objects.create(file=SimpleUploadedFile("h.CalBack", cb.read_bytes()),
                                          est=SimpleUploadedFile("h.est", est.read_bytes()))
        p = Project.objects.create(code="C", name="Cashel")
        up = Upload.objects.create(project=p, kind=Upload.Kind.DRAWING,
                                   file=SimpleUploadedFile("p2.pdf", (CASHEL / "finishes-P2.pdf").read_bytes()))
        pm = Premeasure.objects.create(project=p, title="C P2", drawing=up)
        analyse_premeasure(pm.pk)
        pm.refresh_from_db()
        r = self.client.get(reverse("room_image", args=[pm.pk, 0]))
        self.assertEqual(r["Content-Type"], "image/png")
        self.assertTrue(r.content.startswith(b"\x89PNG"))
        biggest = max(pm.summary["rooms"], key=lambda x: x["area_m2"])
        RoomCheck.objects.create(premeasure=pm, room_id=biggest["id"], decision="by_hand")
        from premeasure_engine.callidus import read_est

        mats = {m.name for m in read_est(est).materials if m.name != "NONE"}
        pm.finish_products = {f["code"]: sorted(mats)[0] for f in pm.summary["finishes"] if f["kind"] == "floor"}
        pm.save()
        write_premeasure(pm.pk)
        pm.refresh_from_db()
        self.assertEqual(pm.status, Premeasure.Status.DONE, pm.error)
        self.assertEqual(pm.written["by_hand_rooms"], 1)
        self.assertAlmostEqual(pm.written["by_hand_m2"], biggest["area_m2"], places=1)
        # the estimator checks it in Callidus and uploads it back: here, unchanged
        import zipfile

        from .models import CheckedJob

        with pm.output.open("rb") as f, zipfile.ZipFile(f) as z:
            name = next(n for n in z.namelist() if n.endswith(".est"))
            data = z.read(name)
        texts = [t.text for t in tender_todo(p)]
        self.assertTrue(any("not back yet" in t for t in texts), texts)
        r = self.client.post(reverse("checked", args=[p.pk]),
                             {"jobs": [SimpleUploadedFile(Path(name).name, data)]})
        self.assertRedirects(r, reverse("checked", args=[p.pk]))
        job = CheckedJob.objects.get()
        self.assertEqual(job.premeasure, pm)  # matched by its name
        self.assertAlmostEqual(job.comparison["right_first_time"], 1.0, places=3)
        self.assertContains(self.client.get(reverse("checked", args=[p.pk])), "100%")
        self.assertContains(self.client.get(reverse("checked_job", args=[job.pk])), "Unchanged")
        # uploading again from the drawing page replaces it
        self.client.post(reverse("checked", args=[p.pk]), {"jobs": [SimpleUploadedFile("anything.est", data)],
                                                           "drawing": str(pm.pk)})
        self.assertEqual(CheckedJob.objects.filter(current=True).count(), 1)
        self.assertFalse(CheckedJob.objects.get(pk=job.pk).current)
        texts = [t.text for t in tender_todo(p)]
        self.assertFalse(any("not back yet" in t for t in texts), texts)
        from openpyxl import load_workbook

        sheet = self.client.get(reverse("crean_quantities", args=[p.pk]))
        ws = load_workbook(io.BytesIO(sheet.content))["ROOMS"]
        self.assertEqual(ws["B1"].value, "Material Name")
        self.assertGreater(ws.max_row, 10)
        self.user.is_staff = True
        self.user.save()
        self.assertContains(self.client.get(reverse("accuracy")), "Right first time")
        # a job that matches no drawing is kept for the spreadsheet, with nothing to compare
        self.client.post(reverse("checked", args=[p.pk]), {"jobs": [SimpleUploadedFile("Other floor.est", est.read_bytes())]})
        other = CheckedJob.objects.filter(current=True, premeasure=None).get()
        self.assertIsNone(other.comparison)
        # reading again numbers the rooms afresh, so checks are cleared
        analyse_premeasure(pm.pk)
        self.assertFalse(RoomCheck.objects.filter(premeasure=pm).exists())


class RevisionNameTest(TestCase):
    def test_revision_read_from_file_names(self):
        from .revisions import drawing_key, split_revision

        self.assertEqual(split_revision("2005-KLA-00-ZZ-DR-A-110 P2.pdf"), ("2005-KLA-00-ZZ-DR-A-110", "P2"))
        self.assertEqual(split_revision("AR-1627_C02.pdf"), ("AR-1627", "C02"))
        self.assertEqual(split_revision("Finishes GF Rev B.pdf"), ("Finishes GF", "B"))
        self.assertEqual(split_revision("plan (T1).pdf"), ("plan", "T1"))
        # a lone letter is part of the name, not a revision: part A and part B are different drawings
        self.assertEqual(split_revision("1st_floor_part_B.pdf"), ("1st_floor_part_B", ""))
        self.assertNotEqual(drawing_key("1st_floor_part_A.pdf"), drawing_key("1st_floor_part_B.pdf"))
        self.assertEqual(drawing_key("A-110 P1.pdf"), drawing_key("A-110_P2.pdf"))


@skipUnless(CASHEL and CASHEL.exists(), "PREMEASURE_FIXTURES not set")
class RevisionFlowTest(TenderBase):
    def setUp(self):
        super().setUp()
        self.inline = override_settings(JOBS_INLINE=True)
        self.inline.enable()
        est = sorted(CASHEL.glob("*.est"))[0]
        cb = sorted(CASHEL.glob("*.CalBack"))[0]
        BackgroundTemplate.objects.create(file=SimpleUploadedFile("h.CalBack", cb.read_bytes()),
                                          est=SimpleUploadedFile("h.est", est.read_bytes()))
        self.project = Project.objects.create(code="C", name="Cashel")

    def tearDown(self):
        self.inline.disable()
        super().tearDown()

    def upload(self, name):
        pdf = (CASHEL / "finishes-P2.pdf").read_bytes()
        self.client.post(reverse("new_premeasure", args=[self.project.pk]),
                         {"drawings": [SimpleUploadedFile(name, pdf)], "notify": ""})
        return Premeasure.objects.order_by("-pk").first()

    def test_new_revision_replaces_the_old_and_keeps_its_checks(self):
        p1 = self.upload("2005-KLA-110 P1.pdf")
        self.assertEqual(p1.status, Premeasure.Status.REVIEW, p1.error)
        flagged = [r["id"] for r in p1.summary["rooms"] if r["status"] != "Verified"]
        RoomCheck.objects.create(premeasure=p1, room_id=flagged[0], decision="ok")
        p2 = self.upload("2005-KLA-110 P2.pdf")
        p1.refresh_from_db()
        self.assertEqual(p2.supersedes, p1)
        self.assertEqual(p2.drawing.revision, "P2")
        self.assertFalse(p1.is_current)
        self.assertEqual(p2.revision_diff["counts"], {"unchanged": len(p1.summary["rooms"])})
        self.assertEqual(p2.revision_diff["carried_checks"], 1)
        self.assertTrue(RoomCheck.objects.filter(premeasure=p2, room_id=flagged[0], decision="ok").exists())
        self.assertEqual(list(self.project.current_drawings()), [p2])
        self.assertNotIn(flagged[0], [r["id"] for r in rooms_to_check(p2)])
        page = self.client.get(reverse("changes", args=[p2.pk]))
        self.assertContains(page, "No rooms changed.")
        self.assertContains(self.client.get(reverse("premeasure", args=[p1.pk])), "Replaced by a later revision")
        self.assertContains(self.client.get(reverse("project", args=[self.project.pk])), "1 earlier revision")
        # the estimator says it is not a revision after all
        self.client.post(reverse("set_revision", args=[p2.pk]), {"replaces": ""})
        p2.refresh_from_db()
        self.assertIsNone(p2.supersedes)
        self.assertEqual(self.project.current_drawings().count(), 2)
        # and links it back by hand
        self.client.post(reverse("set_revision", args=[p2.pk]), {"replaces": str(p1.pk)})
        p2.refresh_from_db()
        self.assertEqual(p2.supersedes, p1)
        self.assertEqual(p2.revision_link, "manual")
        self.assertEqual(p2.revision_diff["carried_checks"], 1)

    def test_named_like_a_revision_but_a_different_drawing(self):
        from premeasure_engine.revisions import RevisionDiff

        p1 = self.upload("Plan rev A.pdf")
        with mock.patch("premeasure_engine.revisions.compare_revisions", return_value=RevisionDiff()):
            p2 = self.upload("Plan rev B.pdf")
        self.assertIsNone(p2.supersedes)
        self.assertEqual(p2.revision_diff["rejected"], p1.title)
        texts = [t.text for t in tender_todo(self.project)]
        self.assertTrue(any("named like a new revision" in t for t in texts), texts)
