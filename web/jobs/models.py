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
    """A product the tender uses, set up once per project (Project setup).

    The name is how it appears in Callidus. If the project's Callidus job (or
    the house template) already has a product of that name it is used as it
    is; otherwise the platform adds it, copying the roll and waste settings
    of ``settings_from``, a product already in that job."""

    class Kind(models.TextChoices):
        FLOOR = "floor", "Floor finish"
        SKIRTING = "skirting", "Skirting or edge"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="products")
    finish_code = models.CharField(
        max_length=30, blank=True, help_text="Code on the drawings, for example F1-D or FF7."
    )
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.FLOOR)
    name = models.CharField("product name in Callidus", max_length=200)
    fill = models.CharField("colour", max_length=7, blank=True, help_text="Colour shown in Callidus, #rrggbb.")
    settings_from = models.CharField(
        max_length=200, blank=True,
        help_text="A product already in the Callidus job whose roll and waste settings a new product copies.",
    )
    colour = models.CharField(max_length=100, blank=True)
    spec_source = models.CharField(max_length=200, blank=True, help_text="Where this came from, and its date.")

    class Meta:
        ordering = ["project", "kind", "finish_code", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "finish_code"], condition=~models.Q(finish_code=""), name="one_product_per_code"
            )
        ]

    def __str__(self) -> str:
        return f"{self.finish_code}: {self.name}" if self.finish_code else self.name

    def save(self, *args, **kwargs):
        # Callidus uses the Windows character set: keep names to what it can show
        import unicodedata

        name = unicodedata.normalize("NFKC", self.name).strip()
        self.name = name.encode("cp1252", "replace").decode("cp1252").replace("<", "(").replace(">", ")")
        super().save(*args, **kwargs)

    @property
    def rgb(self) -> tuple[int, int, int] | None:
        h = self.fill.lstrip("#")
        if len(h) != 6:
            return None
        try:
            return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            return None


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
    """The house Callidus template, set up once in the admin.

    The .est is the starting job for projects with no Callidus job of their
    own: its products are the ones new products copy their settings from. The
    .CalBack must have one sheet; it is the container new backgrounds go in
    (only its header is kept). Any Crean job will do for both."""

    file = models.FileField("background (.CalBack)", upload_to="templates/")
    est = models.FileField(
        "Callidus job (.est)", upload_to="templates/", blank=True,
        help_text="A Crean job with the usual product types (sheet, coved skirting) set up.",
    )
    note = models.CharField(max_length=200, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]
        verbose_name = "Callidus template"

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
        Upload, on_delete=models.PROTECT, related_name="premeasures_started", null=True, blank=True,
        help_text="The project's Callidus job this was written from; empty means the house template.",
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
