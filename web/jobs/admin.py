from django.contrib import admin, messages

from .models import Job, Level, Product, Project, Upload
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
