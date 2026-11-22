from django.contrib import admin, messages

from .models import BackgroundTemplate, Batch, Job, LibraryProduct, Level, MeasuringRules, Premeasure, Product, Project, Upload
from .queue import get_queue


class LevelInline(admin.TabularInline):
    model = Level
    extra = 1


class ProductInline(admin.TabularInline):
    model = Product
    extra = 1


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "client", "main_contractor", "created_at")
    search_fields = ("code", "name", "client")
    inlines = [LevelInline, ProductInline]


@admin.register(Upload)
class UploadAdmin(admin.ModelAdmin):
    list_display = ("project", "kind", "file", "level", "revision", "uploaded_at")
    list_filter = ("kind", "project")
    actions = ["read_callidus_files"]

    @admin.action(description="Read selected Callidus .est files")
    def read_callidus_files(self, request, queryset):
        queue = get_queue()
        count = 0
        for upload in queryset.filter(kind=Upload.Kind.CALLIDUS_EST):
            job = Job.objects.create(upload=upload, task="read_callidus_est")
            queue.enqueue("jobs.tasks.read_callidus_est", job.pk)
            count += 1
        self.message_user(request, f"Queued {count} file(s).", messages.SUCCESS)


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = ("task", "upload", "status", "created_at", "finished_at")
    list_filter = ("status", "task")
    readonly_fields = ("upload", "task", "status", "result", "error", "created_at", "finished_at")


@admin.register(BackgroundTemplate)
class BackgroundTemplateAdmin(admin.ModelAdmin):
    list_display = ("__str__", "est", "file", "uploaded_at")

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        from premeasure_engine.callidus.material_writer import existing_products
        from premeasure_engine.premeasure import calback_sheets

        if obj.est:
            try:
                kinds = {e.kind for e in existing_products(obj.est.path) if e.copyable}
            except Exception as exc:
                self.message_user(request, f"The .est could not be read ({exc}).", messages.ERROR)
                return
            for kind, what in (("sheet", "sheet product"), ("edge", "coved skirting or other edge product")):
                if kind not in kinds:
                    self.message_user(request, f"The .est has no {what} that new products can copy.", messages.WARNING)

        try:
            n = calback_sheets(obj.file.path)
        except Exception as exc:
            self.message_user(request, f"This file could not be read as a .CalBack ({exc}).", messages.ERROR)
            return
        if n != 1:
            self.message_user(request, f"This .CalBack has {n} sheets; a template needs exactly one.", messages.WARNING)


@admin.register(Premeasure)
class PremeasureAdmin(admin.ModelAdmin):
    list_display = ("title", "project", "status", "created_by", "created_at")
    list_filter = ("status", "project")
    readonly_fields = ("summary", "written", "error", "created_at", "updated_at")


@admin.register(LibraryProduct)
class LibraryProductAdmin(admin.ModelAdmin):
    list_display = ("name", "type", "supplier", "wastage_pct", "active")
    list_filter = ("type", "active")
    search_fields = ("name", "supplier", "product_number")


admin.site.register(MeasuringRules)
admin.site.register(Batch)
