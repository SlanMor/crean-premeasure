"""Crean's answers to the measuring questions (November 2026) become the
company rules. A rule someone has already set on the platform is left alone:
only blank rules, and values still at the old defaults, are filled in.

Wastage of 0 was the old "nothing set" default, so it becomes blank (not set)
unless Crean gave a figure for that type."""

from decimal import Decimal

from django.db import migrations

ANSWERS = {
    "room_edge": "wall_face",
    "glazing": "inside_frame",
    "skirting_on_glazing": "no",
    "threshold_strips": "yes",
    "furniture": "kicker_100",
    "stairs": "treads_lm",
    "nosings_separate": "yes",
    "room_splits": "by_tag_doors",
    "coving_notes": "schedule",
    "lift_floors": "once",
}
# Crean gave no figure for tile, matting or trims, so those stay not set.
WASTAGE = {"sheet": Decimal("10"), "carpet_tile": Decimal("5"), "skirting": Decimal("10")}
TYPES = ("sheet", "tile", "carpet_tile", "matwell", "skirting", "edge")

OTHER = """Products follow the main contractor's finishes schedule where there is one. Marked-up Callidus drawings go to the main contractor for sign-off before anything is ordered.
One Callidus file per floor plan.
Treads and risers are measured by the linear metre and added to the spreadsheet by hand, not in Callidus. Nosings are priced separately; their quantity is the same as the risers.
Lifts: measured once, on one floor plan, as the same car serves every floor.
Coved skirting: wet areas, toilets and cleaners' stores as standard. In hospitals expect it everywhere. If the extent is unclear, send an RFI to the main contractor.
Matwells are measured from the drawing. High-value mats are checked on site, as they are made to exact size.
Logos and inlays are priced as extra over items, job by job.
Ordering: tiles and laminate round up to full boxes. Sheet vinyl and carpet are cut from the roll, so no rounding to whole rolls.
Entrance matting wastage depends on size and spec."""


def forwards(apps, schema_editor):
    Rules = apps.get_model("jobs", "MeasuringRules")
    company = Rules.objects.filter(project=None).first() or Rules.objects.create(project=None)
    for field, value in ANSWERS.items():
        current = getattr(company, field)
        if not current or (field == "lift_floors" and current == "yes"):
            setattr(company, field, value)
    if company.min_opening_m2 == Decimal("2"):
        company.min_opening_m2 = Decimal("0.5")
    for t in TYPES:
        f = f"wastage_{t}"
        if getattr(company, f) in (None, Decimal("0")):
            setattr(company, f, WASTAGE.get(t))
    if not company.other_rules:
        company.other_rules = OTHER
    company.save()

    for own in Rules.objects.exclude(project=None):
        for t in TYPES:  # 0 was copied from the old company default: fall back to the company's figure
            if getattr(own, f"wastage_{t}") == Decimal("0"):
                setattr(own, f"wastage_{t}", None)
        if own.lift_floors == "yes":
            own.lift_floors = "once"
        own.save()


class Migration(migrations.Migration):
    dependencies = [("jobs", "0015_crean_rule_options")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
