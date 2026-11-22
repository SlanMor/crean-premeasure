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
    background = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        help_text="For a Callidus .est: its .CalBack background.",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.get_kind_display()}: {self.filename}"

    @property
    def filename(self) -> str:
        return self.file.name.rsplit("/", 1)[-1]


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


class BackgroundTemplate(models.Model):
    """A one-sheet Callidus .CalBack used as the container for new backgrounds.

    Any Crean .CalBack with a single sheet will do: we keep its header and swap
    in the drawing. Used when the project's own .CalBack has several sheets or
    none was uploaded. Set up once in the admin."""

    file = models.FileField(upload_to="templates/")
    note = models.CharField(max_length=200, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self) -> str:
        return self.note or self.file.name


class Premeasure(models.Model):
    """One finishes drawing read and written out as a pre-measured Callidus job."""

    class Status(models.TextChoices):
        READING = "reading", "Reading the drawing"
        REVIEW = "review", "Ready for review"
        WRITING = "writing", "Writing Callidus files"
        DONE = "done", "Callidus files ready"
        FAILED = "failed", "Failed"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="premeasures")
    level = models.CharField(max_length=100, blank=True)
    title = models.CharField(max_length=120, help_text="Name for the Callidus file.")
    drawing = models.ForeignKey(Upload, on_delete=models.PROTECT, related_name="premeasures")
    page = models.PositiveIntegerField(default=1)
    starter = models.ForeignKey(
        Upload, on_delete=models.PROTECT, related_name="premeasures_started",
        help_text="Callidus .est holding this job's products.",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.READING)
    summary = models.JSONField(null=True, blank=True)
    finish_products = models.JSONField(default=dict, blank=True)
    skirting_products = models.JSONField(default=dict, blank=True)
    analysis_file = models.FileField(upload_to="premeasure/work/", blank=True)
    preview = models.FileField(upload_to="premeasure/preview/", blank=True)
    output = models.FileField(upload_to="premeasure/output/", blank=True)
    written = models.JSONField(null=True, blank=True)
    error = models.TextField(blank=True)
    created_by = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.project.code} {self.title}"

    @property
    def busy(self) -> bool:
        return self.status in (self.Status.READING, self.Status.WRITING)
