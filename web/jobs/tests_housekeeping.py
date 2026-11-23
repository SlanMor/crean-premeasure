"""Backups and restoring, deleting, retention, and alerts."""

import os
import shutil
import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from django.contrib.auth.models import User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (Alert, BackupRun, Company, DeletionLog, LibraryProduct, Premeasure, Product, Project,
                     RoomCheck, SystemSettings, Upload)


class Base(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.store = tempfile.mkdtemp()
        self.s = override_settings(MEDIA_ROOT=self.media, JOBS_INLINE=True,
                                   EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
                                   EMAIL_HOST="smtp.test")
        self.s.enable()
        self.user = User.objects.create_user("boss", email="boss@crean.ie", is_staff=True)
        self.client.force_login(self.user)

    def tearDown(self):
        self.s.disable()
        shutil.rmtree(self.media, ignore_errors=True)
        shutil.rmtree(self.store, ignore_errors=True)

    def drawing(self, project, name="A-110 P1.pdf", upload=None, **kw):
        up = upload or Upload.objects.create(project=project, kind=Upload.Kind.DRAWING, drawing_number=name,
                                             file=SimpleUploadedFile(name, b"%PDF-1.4 " + b"x" * 5000))
        pm = Premeasure.objects.create(project=project, title=name, drawing=up, status=Premeasure.Status.REVIEW,
                                       summary={"rooms": [], "finishes": []}, **kw)
        pm.preview.save("p.png", SimpleUploadedFile("p.png", b"png" * 100), save=False)
        pm.analysis_file.save("a.pickle", SimpleUploadedFile("a.pickle", b"a" * 2000), save=False)
        pm.save()
        return pm


class BackupTest(Base):
    def test_backup_and_restore(self):
        from .backup import restore, run_backup

        p = Project.objects.create(code="T1", name="School", client=Company.objects.create(kind="client", name="DoE"))
        Product.objects.create(project=p, finish_code="FF1", library=LibraryProduct.objects.create(name="Lino"))
        pm = self.drawing(p)
        with mock.patch.dict(os.environ, {"BACKUP_DIR": self.store}):
            run = run_backup()
            self.assertTrue(run.ok, run.error)
            self.assertEqual(len(list(Path(self.store, "database").iterdir())), 1)
            backed = {q.relative_to(self.store).as_posix() for q in Path(self.store, "media").rglob("*") if q.is_file()}
            self.assertIn(f"media/{pm.drawing.file.name}", backed)
            self.assertFalse(any("premeasure/work/" in b or "premeasure/preview/" in b for b in backed))
            # a second night sends nothing new
            self.assertEqual(run_backup().files_sent, 0)
            # lose everything, then restore
            pdf = Path(pm.drawing.file.path)
            RoomCheck.objects.all().delete()
            Premeasure.objects.all().delete()
            Upload.objects.all().delete()
            Product.objects.all().delete()
            Project.objects.all().delete()
            Company.objects.all().delete()
            LibraryProduct.objects.all().delete()
            pdf.unlink()
            restore()
        p = Project.objects.get(code="T1")
        self.assertEqual(p.client.name, "DoE")
        self.assertEqual(p.products.get().library.name, "Lino")
        self.assertTrue(Path(p.premeasures.get().drawing.file.path).exists())

    def test_dumps_kept_daily_then_monthly(self):
        from .backup import _keep

        today = date(2026, 11, 23)
        keys = [f"database/{(today - timedelta(days=n)).isoformat()}-0200.json.gz" for n in range(400)]
        keep = _keep(keys, today)
        self.assertEqual(len([k for k in keep if (today - date.fromisoformat(k[9:19])).days < 14]), 14)
        firsts = sorted(k for k in keep if k[17:19] == "01")
        self.assertEqual(len(firsts), 12)
        self.assertEqual(len(keep), 14 + 12)  # 10 to 23 November, and the 1st of December 2025 to November 2026

    def test_deleted_files_leave_the_backup_after_30_days(self):
        from .backup import run_backup

        p = Project.objects.create(code="T", name="t")
        pm = self.drawing(p)
        key = f"media/{pm.drawing.file.name}"
        with mock.patch.dict(os.environ, {"BACKUP_DIR": self.store}):
            run_backup()
            Path(pm.drawing.file.path).unlink()
            run_backup(now=timezone.now() + timedelta(days=1))
            self.assertTrue(Path(self.store, key).exists())  # still there for a month
            run_backup(now=timezone.now() + timedelta(days=32))
            self.assertFalse(Path(self.store, key).exists())

    def test_s3(self):
        try:
            import boto3
            from moto import mock_aws
        except ImportError:
            self.skipTest("moto (for a stand-in S3) not installed")

        from .backup import run_backup

        env = {"BACKUP_S3_BUCKET": "crean-backup", "BACKUP_S3_ACCESS_KEY": "k", "BACKUP_S3_SECRET_KEY": "s",
               "BACKUP_S3_REGION": "us-east-1"}
        self.drawing(Project.objects.create(code="T", name="t"))
        with mock_aws(), mock.patch.dict(os.environ, env):
            boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="crean-backup")
            run = run_backup()
            keys = [o["Key"] for o in boto3.client("s3", region_name="us-east-1")
                    .list_objects_v2(Bucket="crean-backup")["Contents"]]
        self.assertTrue(run.ok, run.error)
        self.assertTrue(any(k.startswith("database/") for k in keys))
        self.assertTrue(any(k.startswith("media/uploads/") for k in keys))

    def test_no_target_is_an_alert(self):
        from .alerts import check_all

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BACKUP_DIR", None)
            os.environ.pop("BACKUP_S3_BUCKET", None)
            found = check_all()
        self.assertIn("Backups are not set up", found)
        self.assertEqual(mail.outbox[-1].to, ["boss@crean.ie"])


class DeleteTest(Base):
    def test_delete_drawings_with_their_files(self):
        p = Project.objects.create(code="T", name="t")
        p1 = self.drawing(p, "A-110 P1.pdf")
        p2 = self.drawing(p, "A-110 P2.pdf", supersedes=p1, revision_link="auto",
                          revision_diff={"counts": {"unchanged": 1}})
        RoomCheck.objects.create(premeasure=p2, room_id=0, decision="ok")
        other_page = self.drawing(p, "Pack.pdf")
        second_page = Premeasure.objects.create(project=p, title="Pack p2", drawing=other_page.drawing, page=2)
        files = [Path(p2.preview.path), Path(p2.analysis_file.path), Path(p2.drawing.file.path)]
        page = self.client.get(reverse("delete_drawings", args=[p.pk]) + f"?ids={p2.pk}&ids={other_page.pk}")
        self.assertContains(page, "becomes current again")
        self.assertContains(page, "The PDF stays")
        r = self.client.post(reverse("delete_drawings", args=[p.pk]),
                             {"ids": [p2.pk, other_page.pk], "confirm": "yes"})
        self.assertRedirects(r, reverse("project", args=[p.pk]))
        self.assertFalse(any(f.exists() for f in files))
        self.assertEqual(list(p.current_drawings().order_by("pk")), [p1, second_page])  # P1 is current again
        self.assertTrue(Path(second_page.drawing.file.path).exists())  # still used by page 2
        self.assertEqual(DeletionLog.objects.filter(by=self.user).count(), 2)

    def test_delete_tender_or_only_its_files(self):
        p = Project.objects.create(code="T9", name="t")
        Product.objects.create(project=p, finish_code="FF1")
        pm = self.drawing(p)
        pdf = Path(pm.drawing.file.path)
        self.client.post(reverse("delete_tender", args=[p.pk]), {"code": "wrong", "what": "all"})
        self.assertTrue(Project.objects.filter(pk=p.pk).exists())
        self.client.post(reverse("delete_tender", args=[p.pk]), {"code": "T9", "what": "files"})
        p.refresh_from_db()
        self.assertFalse(pdf.exists())
        self.assertFalse(p.premeasures.exists())
        self.assertTrue(p.products.exists())
        self.assertIsNotNone(p.files_removed_at)
        self.client.post(reverse("delete_tender", args=[p.pk]), {"code": "T9", "what": "all"})
        self.assertFalse(Project.objects.filter(pk=p.pk).exists())

    def test_tender_deletion_is_for_administrators(self):
        p = Project.objects.create(code="T", name="t")
        self.client.force_login(User.objects.create_user("est"))
        self.assertEqual(self.client.get(reverse("delete_tender", args=[p.pk])).status_code, 302)


class RetentionTest(Base):
    def test_clean_up_by_the_settings(self):
        from .housekeeping import cleanup, plan_cleanup

        old = timezone.now() - timedelta(days=400)
        s = SystemSettings.get()
        s.working_files_days, s.replaced_days, s.tender_months = 60, 30, 6
        s.save()
        quiet = Project.objects.create(code="Q", name="quiet")
        kept = Project.objects.create(code="K", name="kept", keep_files=True)
        busy = Project.objects.create(code="B", name="busy")
        for p in (quiet, kept, busy):
            self.drawing(p)
        b1 = self.drawing(busy, "X P1.pdf")
        b2 = self.drawing(busy, "X P2.pdf", supersedes=b1)
        Premeasure.objects.filter(project__in=[quiet, kept]).update(updated_at=old)
        Project.objects.filter(pk__in=[quiet.pk, kept.pk]).update(created_at=old)
        Upload.objects.filter(project__in=[quiet, kept]).update(uploaded_at=old)
        Premeasure.objects.filter(pk=b2.pk).update(created_at=old)  # replaced long ago
        stale = busy.premeasures.exclude(pk__in=[b1.pk, b2.pk]).get()
        Premeasure.objects.filter(pk=stale.pk).update(updated_at=old)
        plan = plan_cleanup()
        self.assertEqual(plan.tenders, [quiet])
        self.assertEqual(plan.replaced, [b1])
        self.assertIn(stale.pk, [pm.pk for pm in plan.working])
        self.assertNotIn(kept, plan.tenders)  # its working files may go (they are made again), never its drawings
        self.assertContains(self.client.get(reverse("system")), "1 replaced revision")
        self.assertGreater(cleanup(), 0)
        self.assertFalse(quiet.premeasures.exists())
        self.assertTrue(kept.premeasures.exists())
        self.assertFalse(Premeasure.objects.filter(pk=b1.pk).exists())
        stale.refresh_from_db()
        self.assertFalse(stale.analysis_file)
        self.assertTrue(stale.preview)  # the small picture stays
        self.assertTrue(plan_cleanup().empty)


class AlertTest(Base):
    def test_stuck_drawings_alert_once_then_clear(self):
        from .alerts import check_all

        SystemSettings.objects.create(alert_emails="ops@crean.ie\nnot-an-address")
        p = Project.objects.create(code="T", name="t")
        pm = self.drawing(p)
        Premeasure.objects.filter(pk=pm.pk).update(status=Premeasure.Status.READING,
                                                   updated_at=timezone.now() - timedelta(hours=2))
        with mock.patch.dict(os.environ, {"BACKUP_DIR": self.store}):
            BackupRun.objects.create(ok=True)
            check_all()
            check_all()
            stuck = [m for m in mail.outbox if "waiting for over 45 minutes" in m.subject]
            self.assertEqual(len(stuck), 1)
            self.assertEqual(stuck[0].to, ["ops@crean.ie"])
            Premeasure.objects.filter(pk=pm.pk).update(status=Premeasure.Status.REVIEW)
            check_all()
        self.assertTrue(any(m.subject.startswith("[Crean pre-measure] Resolved") for m in mail.outbox))
        self.assertIsNotNone(Alert.objects.get(key="stuck").resolved_at)

    def test_program_error_on_a_drawing_alerts(self):
        from .tasks import _fail

        pm = self.drawing(Project.objects.create(code="T", name="t"))
        _fail(pm, KeyError("boom"))
        self.assertTrue(any("A drawing failed" in m.subject for m in mail.outbox))
        n = len(mail.outbox)
        from premeasure_engine.drawing.pdf_reader import ScaleNotFound

        _fail(pm, ScaleNotFound("No drawing scale"))  # the estimator can fix this: no alert
        self.assertEqual(len(mail.outbox), n)

    def test_system_page_and_test_alert(self):
        self.assertContains(self.client.get(reverse("system")), "Backups")
        self.client.post(reverse("system"), {"action": "test_alert"})
        self.assertTrue(any("Test alert" in m.subject for m in mail.outbox))
