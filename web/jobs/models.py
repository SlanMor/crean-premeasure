"""Project setup and processing jobs.

First cut of the "Project setup" module in the functional spec: a project,
its levels, its approved products per finish code, and the files uploaded to
it. Measuring rules and house types / plot schedules follow in later phases.
"""

from django.db import models


class Project(models.Model):
    code = models.CharField("project ID", max_length=50, unique=True)
    name = models.CharField(max_length=200)
    client = models.CharField(max_length=200, blank=True)
    main_contractor = models.CharField(max_length=200, blank=True)
    site = models.CharField(max_length=300, blank=True)
    room_naming = models.CharField(
        max_length=200, blank=True, help_text="Room naming convention, for example 'code then name'."
    )
    notes = models.TextField(blank=True, help_text="Scope notes: exclusions, glazing, recesses and similar.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.code} {self.name}"


class Level(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="levels")
    name = models.CharField(max_length=100)
    order = models.IntegerField(default=0)

    class Meta:
        ordering = ["project", "order"]
        unique_together = [("project", "name")]

    def __str__(self) -> str:
        return f"{self.project.code} / {self.name}"


class Product(models.Model):
    """An approved product for a finish code on this project."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="products")
    finish_code = models.CharField(max_length=30, help_text="Code on the drawing, for example F1-D or FF7.")
    name = models.CharField(max_length=200)
    colour = models.CharField(max_length=100, blank=True)
    callidus_material = models.CharField(max_length=200, blank=True)
    spec_source = models.CharField(max_length=200, blank=True, help_text="Where this came from, and its date.")

    class Meta:
        ordering = ["project", "finish_code"]
        unique_together = [("project", "finish_code")]

    def __str__(self) -> str:
        return f"{self.finish_code}: {self.name}"


class Upload(models.Model):
    class Kind(models.TextChoices):
        DRAWING = "drawing", "Drawing"
        CALLIDUS_EST = "est", "Callidus .est"
        CALLIDUS_BACKGROUND = "calback", "Callidus .CalBack"
        MEASURE_SHEET = "xlsx", "Measure spreadsheet"
        BOQ = "boq", "BOQ"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="uploads")
    level = models.ForeignKey(Level, on_delete=models.SET_NULL, null=True, blank=True)
    kind = models.CharField(max_length=20, choices=Kind.choices)
    file = models.FileField(upload_to="uploads/%Y/%m/")
    drawing_number = models.CharField(max_length=100, blank=True)
    revision = models.CharField(max_length=30, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.get_kind_display()}: {self.file.name}"


class Job(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"

    upload = models.ForeignKey(Upload, on_delete=models.CASCADE, related_name="jobs")
    task = models.CharField(max_length=50)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    result = models.JSONField(null=True, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.task} on {self.upload} ({self.status})"
