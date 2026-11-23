import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def companies_from_text(apps, schema_editor):
    """Projects named their client and main contractor in free text: make each a company."""
    Company = apps.get_model("jobs", "Company")
    Project = apps.get_model("jobs", "Project")
    for p in Project.objects.all():
        for text_field, fk, kind in (("client", "client_co", "client"), ("main_contractor", "contractor_co", "contractor")):
            name = (getattr(p, text_field) or "").strip()
            if name:
                co, _ = Company.objects.get_or_create(kind=kind, name=name[:200])
                setattr(p, fk, co)
        p.save()


class Migration(migrations.Migration):
    dependencies = [
        ("jobs", "0010_project_shortlist"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Company",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("kind", models.CharField(choices=[("client", "Client"), ("contractor", "Main contractor"), ("architect", "Architect")], max_length=20)),
                ("name", models.CharField(max_length=200)),
                ("contact_name", models.CharField(blank=True, max_length=200, verbose_name="contact")),
                ("email", models.EmailField(blank=True, max_length=254)),
                ("phone", models.CharField(blank=True, max_length=50)),
                ("notes", models.TextField(blank=True, help_text="For example how they lay out their drawings, or who to ask.")),
                ("active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["kind", "name"], "verbose_name_plural": "companies"},
        ),
        migrations.AddConstraint(
            model_name="company",
            constraint=models.UniqueConstraint(fields=("kind", "name"), name="one_company_per_kind"),
        ),
        migrations.AddField(
            model_name="project", name="client_co",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name="client_of", to="jobs.company", limit_choices_to={"kind": "client"}),
        ),
        migrations.AddField(
            model_name="project", name="contractor_co",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name="contractor_of", to="jobs.company", limit_choices_to={"kind": "contractor"}),
        ),
        migrations.RunPython(companies_from_text, migrations.RunPython.noop),
        migrations.RemoveField(model_name="project", name="client"),
        migrations.RemoveField(model_name="project", name="main_contractor"),
        migrations.RenameField(model_name="project", old_name="client_co", new_name="client"),
        migrations.RenameField(model_name="project", old_name="contractor_co", new_name="main_contractor"),
        migrations.AddField(
            model_name="project", name="architect",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name="architect_of", to="jobs.company", limit_choices_to={"kind": "architect"}),
        ),
        migrations.AddField(model_name="project", name="due",
                            field=models.DateField(blank=True, null=True, verbose_name="tender due")),
        migrations.AddField(
            model_name="project", name="setup_step",
            field=models.CharField(choices=[("rules", "Tender questions"), ("drawings", "Drawings"), ("products", "Products"), ("done", "Set up")], default="done", max_length=20),
        ),
        migrations.CreateModel(
            name="RoomCheck",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("room_id", models.PositiveIntegerField()),
                ("decision", models.CharField(choices=[("ok", "Outline right"), ("by_hand", "Wrong: measure by hand")], max_length=10)),
                ("note", models.CharField(blank=True, help_text="What was wrong, so the software can learn.", max_length=300)),
                ("at", models.DateTimeField(auto_now=True)),
                ("by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("premeasure", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="checks", to="jobs.premeasure")),
            ],
            options={"ordering": ["premeasure", "room_id"]},
        ),
        migrations.AddConstraint(
            model_name="roomcheck",
            constraint=models.UniqueConstraint(fields=("premeasure", "room_id"), name="one_check_per_room"),
        ),
    ]
