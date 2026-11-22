from django.db import migrations


def to_list(apps, schema_editor):
    """Each project product becomes an entry in the company product list."""
    Product = apps.get_model("jobs", "Product")
    LibraryProduct = apps.get_model("jobs", "LibraryProduct")
    for p in Product.objects.all():
        name = (p.old_name or "").strip()
        if name:
            lib, _ = LibraryProduct.objects.get_or_create(
                name=name[:200],
                defaults={"type": "skirting" if p.kind == "skirting" else "sheet", "fill": p.fill or "",
                          "callidus_settings_from": p.settings_from or ""},
            )
            p.library = lib
        if not p.finish_code:
            p.delete()  # a product with no finish code now lives in the list only
            continue
        p.save()


class Migration(migrations.Migration):
    dependencies = [("jobs", "0004_product_list")]
    operations = [migrations.RunPython(to_list, migrations.RunPython.noop)]
