"""Project setup and processing jobs.

Projects, their setup (products per finish code, measuring rules), Crean's
product list, uploads and the drawings read for them. House types and plot
schedules follow in later phases.
"""

from django.db import models


class Company(models.Model):
    """A client, main contractor or architect, kept once and picked from a list."""

    class Kind(models.TextChoices):
        CLIENT = "client", "Client"
        CONTRACTOR = "contractor", "Main contractor"
        ARCHITECT = "architect", "Architect"

    kind = models.CharField(max_length=20, choices=Kind.choices)
    name = models.CharField(max_length=200)
    contact_name = models.CharField("contact", max_length=200, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    notes = models.TextField(blank=True, help_text="For example how they lay out their drawings, or who to ask.")
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["kind", "name"]
        verbose_name_plural = "companies"
        constraints = [models.UniqueConstraint(fields=["kind", "name"], name="one_company_per_kind")]

    def __str__(self) -> str:
        return self.name


class Project(models.Model):
    class Step(models.TextChoices):
        RULES = "rules", "Tender questions"
        DRAWINGS = "drawings", "Drawings"
        PRODUCTS = "products", "Products"
        DONE = "done", "Set up"

    code = models.CharField("project ID", max_length=50, unique=True)
    name = models.CharField(max_length=200)
    client = models.ForeignKey(Company, on_delete=models.PROTECT, null=True, blank=True, related_name="client_of",
                               limit_choices_to={"kind": "client"})
    main_contractor = models.ForeignKey(Company, on_delete=models.PROTECT, null=True, blank=True,
                                        related_name="contractor_of", limit_choices_to={"kind": "contractor"})
    architect = models.ForeignKey(Company, on_delete=models.PROTECT, null=True, blank=True,
                                  related_name="architect_of", limit_choices_to={"kind": "architect"})
    due = models.DateField("tender due", null=True, blank=True)
    site = models.CharField(max_length=300, blank=True)
    room_naming = models.CharField(
        max_length=200, blank=True, help_text="Room naming convention, for example 'code then name'."
    )
    notes = models.TextField(blank=True, help_text="Scope notes: exclusions, glazing, recesses and similar.")
    shortlist = models.ManyToManyField(
        "LibraryProduct", blank=True, related_name="shortlisted_in",
        help_text="Products from the list this tender uses, shown first wherever a product is chosen.",
    )
    setup_step = models.CharField(max_length=20, choices=Step.choices, default=Step.DONE)
    keep_files = models.BooleanField(
        "keep this tender's files", default=False,
        help_text="Never remove its drawings and files when tenders are cleaned up.",
    )
    files_removed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def current_drawings(self):
        """The tender's drawings, without those replaced by a later revision."""
        return self.premeasures.filter(superseded_by__isnull=True)

    def project_products(self):
        """This project's products: its shortlist and every product given to a finish code."""
        ids = set(self.shortlist.values_list("id", flat=True))
        ids |= set(self.products.exclude(library=None).values_list("library_id", flat=True))
        return LibraryProduct.objects.filter(id__in=ids, active=True)

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


def callidus_text(text: str) -> str:
    """Callidus uses the Windows character set: keep names to what it can show."""
    import unicodedata

    text = unicodedata.normalize("NFKC", text).strip()
    return text.encode("cp1252", "replace").decode("cp1252").replace("<", "(").replace(">", ")")


class LibraryProduct(models.Model):
    """A product in Crean's product list, shared by every project.

    The name is how it appears in Callidus. Settings here feed the quantities
    (wastage, rounding). When a project's Callidus job lacks the product, the
    platform adds it, copying Callidus's own roll settings from a product of
    the same type in the house template (or the one named in
    ``callidus_settings_from``)."""

    class Type(models.TextChoices):
        SHEET = "sheet", "Sheet (roll)"
        TILE = "tile", "Tile"
        CARPET_TILE = "carpet_tile", "Carpet tile"
        MATWELL = "matwell", "Matwell or matting"
        SKIRTING = "skirting", "Skirting or coving"
        EDGE = "edge", "Trim or threshold strip"

    FLOOR_TYPES = ("sheet", "tile", "carpet_tile", "matwell")

    name = models.CharField("name in Callidus", max_length=200, unique=True)
    type = models.CharField(max_length=20, choices=Type.choices, default=Type.SHEET)
    supplier = models.CharField(max_length=100, blank=True)
    product_number = models.CharField(max_length=60, blank=True, help_text="For example 3756 or AQI2005.")
    colour_name = models.CharField("colour", max_length=100, blank=True)
    fill = models.CharField("colour in Callidus", max_length=7, blank=True, help_text="#rrggbb")
    roll_width_m = models.DecimalField("roll width (m)", max_digits=5, decimal_places=2, null=True, blank=True)
    tile_width_mm = models.PositiveIntegerField("tile width (mm)", null=True, blank=True)
    tile_length_mm = models.PositiveIntegerField("tile length (mm)", null=True, blank=True)
    wastage_pct = models.DecimalField(
        "wastage (%)", max_digits=5, decimal_places=1, null=True, blank=True,
        help_text="On top of the Callidus layout. Leave blank to use the company default for this type.",
    )
    round_up = models.BooleanField("order in whole rolls or boxes", default=False)
    coving_height_mm = models.PositiveIntegerField("coving height (mm)", null=True, blank=True)
    spec_reference = models.CharField(max_length=200, blank=True)
    callidus_settings_from = models.CharField(
        "Callidus settings copied from", max_length=200, blank=True,
        help_text="Advanced. Leave blank and the platform picks a product of the same type in the Callidus template.",
    )
    notes = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    created_by = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "product"

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        self.name = callidus_text(self.name)
        super().save(*args, **kwargs)

    @property
    def is_floor(self) -> bool:
        return self.type in self.FLOOR_TYPES

    @property
    def rgb(self) -> tuple[int, int, int] | None:
        h = self.fill.lstrip("#")
        if len(h) != 6:
            return None
        try:
            return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            return None

    @property
    def size(self) -> str:
        if self.roll_width_m:
            return f"{self.roll_width_m:g} m roll"
        if self.tile_width_mm and self.tile_length_mm:
            return f"{self.tile_width_mm} x {self.tile_length_mm} mm"
        if self.coving_height_mm:
            return f"{self.coving_height_mm} mm high"
        return ""


class Product(models.Model):
    """Project setup: which product the tender uses for each finish code."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="products")
    finish_code = models.CharField(max_length=30, help_text="Code on the drawings, for example F1-D or FF7.")
    library = models.ForeignKey(
        LibraryProduct, on_delete=models.PROTECT, null=True, blank=True, related_name="uses", verbose_name="product"
    )
    description = models.CharField(max_length=300, blank=True, help_text="What the drawing's legend says.")
    kind = models.CharField(max_length=10, choices=[("floor", "Floor"), ("skirting", "Skirting")], default="floor")
    spec_source = models.CharField(max_length=200, blank=True, help_text="Where this came from, and its date.")

    class Meta:
        ordering = ["project", "finish_code"]
        constraints = [models.UniqueConstraint(fields=["project", "finish_code"], name="one_product_per_code")]

    def __str__(self) -> str:
        return f"{self.finish_code}: {self.name or 'not chosen'}"

    @property
    def name(self) -> str:
        return self.library.name if self.library else ""


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
        QUEUED = "queued", "Waiting its turn"
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
    scale = models.PositiveIntegerField(
        null=True, blank=True, help_text="Drawing scale 1:N, only when it is not printed on the sheet."
    )
    sheet = models.CharField(
        max_length=2, blank=True, choices=[(s, s) for s in ("A0", "A1", "A2", "A3", "A4")],
        help_text="Sheet size, only for a drawing saved as a picture whose title block does not show it.",
    )
    starter = models.ForeignKey(
        Upload, on_delete=models.PROTECT, related_name="premeasures_started", null=True, blank=True,
        help_text="The project's Callidus job this was written from; empty means the house template.",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.READING)
    summary = models.JSONField(null=True, blank=True)
    finish_products = models.JSONField(default=dict, blank=True)  # as last written
    skirting_products = models.JSONField(default=dict, blank=True)
    # Products chosen on this drawing that differ from the project's products
    # ({"floor": {code: name}, "skirting": {...}}; "" means left out here).
    # Everything else follows the project's products as they are now.
    chosen_here = models.JSONField(default=dict, blank=True)
    analysis_file = models.FileField(upload_to="premeasure/work/", blank=True)
    preview = models.FileField(upload_to="premeasure/preview/", blank=True)
    output = models.FileField(upload_to="premeasure/output/", blank=True)
    written = models.JSONField(null=True, blank=True)
    error = models.TextField(blank=True)
    created_by = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True)
    batch = models.ForeignKey("Batch", on_delete=models.SET_NULL, null=True, blank=True, related_name="premeasures")
    # an earlier revision of the same drawing, which this one replaces
    supersedes = models.OneToOneField("self", on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name="superseded_by")
    revision_link = models.CharField(max_length=10, blank=True, help_text="auto (by name) or manual")
    revision_diff = models.JSONField(null=True, blank=True)
    changes = models.FileField(upload_to="premeasure/changes/", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.project.code} {self.title}"

    @property
    def is_current(self) -> bool:
        """Not replaced by a later revision."""
        try:
            return self.superseded_by is None
        except Premeasure.DoesNotExist:
            return True

    @property
    def busy(self) -> bool:
        return self.status in (self.Status.QUEUED, self.Status.READING, self.Status.WRITING)

    @property
    def needs_scale(self) -> bool:
        return self.status == self.Status.FAILED and (
            self.error.startswith("No drawing scale") or self.needs_sheet
        )

    @property
    def needs_sheet(self) -> bool:
        """A picture of a drawing: measured once its sheet size and scale are known."""
        return self.status == self.Status.FAILED and "Enter the sheet size" in self.error

    @property
    def plain_error(self) -> bool:
        """A failure explained in plain words (not a program error)."""
        return self.error.startswith(("No drawing scale", "This PDF is a picture"))


class Batch(models.Model):
    """Several drawings uploaded together and left to run."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="batches")
    created_by = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    auto_write = models.BooleanField(
        "write Callidus files automatically", default=True,
        help_text="When every finish on a drawing has a product in the project setup.",
    )
    notify = models.BooleanField("email me when they are all done", default=True)
    notified_at = models.DateTimeField(null=True, blank=True)
    files_pending = models.PositiveIntegerField(default=0, help_text="PDFs still to be split into pages.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "batches"


class MeasuringRules(models.Model):
    """How Crean measures. One row with no project holds the company defaults;
    a project may have its own row that replaces them for that project."""

    ROOM_EDGE = [("", "Not decided yet"), ("wall_face", "Wall face"), ("inside_band", "Inside the skirting band")]
    GLAZING = [("", "Not decided yet"), ("glass_line", "Glass line"), ("inside_frame", "Inside of the frame")]
    DOORWAYS = [("", "Not decided yet"), ("centre", "Centre of the door"), ("under_door", "Under the door to the far face"),
                ("frame", "Stop at the frame")]
    FURNITURE = [("", "Not decided yet"), ("under", "Floor runs under"), ("plinth", "Stop at the plinth"),
                 ("kicker_100", "100 mm in from the kick board")]
    STAIRS = [("", "Not decided yet"), ("plan", "Plan area"), ("treads", "Landings plus treads and risers"),
              ("treads_lm", "Landings by area; treads and risers by linear metre, added by hand")]
    SPLITS = [("", "Not decided yet"), ("continuous", "One area per continuous floor"),
              ("by_tag", "Split at each room tag"),
              ("by_tag_doors", "Split at each room tag, and corridors at doorways")]
    COVING = [("", "Not decided yet"), ("note", "Follow the note"), ("plan", "Follow the plan"),
              ("schedule", "Follow the finishes schedule; query the main contractor if unclear")]
    LIFTS = [("", "Not decided yet"), ("once", "Yes, once, on one floor"), ("each", "Yes, on every floor"),
             ("no", "No")]
    SHEETS = [("", "Not decided yet"), ("one", "One Callidus file"), ("separate", "One file per sheet")]
    YES_NO = [("", "Not decided yet"), ("yes", "Yes"), ("no", "No")]

    project = models.OneToOneField(Project, on_delete=models.CASCADE, null=True, blank=True, related_name="rules")
    room_edge = models.CharField("room edge", max_length=20, choices=ROOM_EDGE, blank=True)
    glazing = models.CharField("floor-to-ceiling glazing: measure to", max_length=20, choices=GLAZING, blank=True)
    skirting_on_glazing = models.CharField("skirting along glazing", max_length=5, choices=YES_NO, blank=True)
    doorways = models.CharField("doorways: floor runs to", max_length=20, choices=DOORWAYS, blank=True)
    threshold_strips = models.CharField("threshold strip at every change of finish", max_length=5, choices=YES_NO,
                                        blank=True)
    min_opening_m2 = models.DecimalField(
        "smallest opening deducted (m²)", max_digits=5, decimal_places=2, default=0.5,
        help_text="Columns, risers or ducts smaller than this are measured through.",
    )
    furniture = models.CharField("fixed furniture and kitchen units", max_length=20, choices=FURNITURE, blank=True)
    stairs = models.CharField("stairs", max_length=20, choices=STAIRS, blank=True)
    nosings_separate = models.CharField("nosings priced separately", max_length=5, choices=YES_NO, blank=True)
    room_splits = models.CharField("room splits", max_length=20, choices=SPLITS, blank=True)
    coving_notes = models.CharField("coving note on the drawing vs the plan", max_length=20, choices=COVING, blank=True)
    lift_floors = models.CharField("add a floor for each lift car", max_length=5, choices=LIFTS, blank=True)
    two_sheets = models.CharField("a floor drawn on two sheets", max_length=20, choices=SHEETS, blank=True)
    wastage_sheet = models.DecimalField("sheet wastage (%)", max_digits=5, decimal_places=1, null=True, blank=True,
                                        help_text="Blank: not set, and the quantities say so.")
    wastage_tile = models.DecimalField("tile wastage (%)", max_digits=5, decimal_places=1, null=True, blank=True,
                                        help_text="Blank: not set, and the quantities say so.")
    wastage_carpet_tile = models.DecimalField("carpet tile wastage (%)", max_digits=5, decimal_places=1, null=True, blank=True,
                                        help_text="Blank: not set, and the quantities say so.")
    wastage_matwell = models.DecimalField("matting wastage (%)", max_digits=5, decimal_places=1, null=True, blank=True,
                                        help_text="Blank: not set, and the quantities say so.")
    wastage_skirting = models.DecimalField("skirting wastage (%)", max_digits=5, decimal_places=1, null=True, blank=True,
                                        help_text="Blank: not set, and the quantities say so.")
    wastage_edge = models.DecimalField("trim wastage (%)", max_digits=5, decimal_places=1, null=True, blank=True,
                                        help_text="Blank: not set, and the quantities say so.")
    other_rules = models.TextField(
        "other rules", blank=True,
        help_text="Anything else about how Crean measures and prices, for the estimator to read.")
    updated_at = models.DateTimeField(auto_now=True)

    # what the software uses today; the rest is recorded and applied as it is built
    APPLIED = {"min_opening_m2", "wastage_sheet", "wastage_tile", "wastage_carpet_tile",
               "wastage_matwell", "wastage_skirting", "wastage_edge"}

    class Meta:
        verbose_name_plural = "measuring rules"

    @classmethod
    def company(cls) -> "MeasuringRules":
        obj = cls.objects.filter(project=None).first()
        return obj or cls.objects.create(project=None)

    @classmethod
    def for_project(cls, project) -> "MeasuringRules":
        own = cls.objects.filter(project=project).first()
        return own or cls.company()

    def wastage_for(self, product_type: str):
        """The allowance for a product type, or None when Crean has not set one.
        A tender's own rules fall back to the company's for a type left blank."""
        pct = getattr(self, f"wastage_{product_type}", None)
        if pct is None and self.project_id:
            return getattr(MeasuringRules.company(), f"wastage_{product_type}", None)
        return pct


class RoomCheck(models.Model):
    """The estimator's decision on one room of a drawing (by its number in the
    drawing's summary). Cleared when the drawing is read again."""

    class Decision(models.TextChoices):
        OK = "ok", "Outline right"
        BY_HAND = "by_hand", "Wrong: measure by hand"

    premeasure = models.ForeignKey(Premeasure, on_delete=models.CASCADE, related_name="checks")
    room_id = models.PositiveIntegerField()
    decision = models.CharField(max_length=10, choices=Decision.choices)
    note = models.CharField(max_length=300, blank=True, help_text="What was wrong, so the software can learn.")
    by = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["premeasure", "room_id"]
        constraints = [models.UniqueConstraint(fields=["premeasure", "room_id"], name="one_check_per_room")]


class CheckedJob(models.Model):
    """A Callidus job as the estimator saved it after checking: the final
    measure. Crean's quantities come from these, and each is compared with the
    drawing's pre-measure to see how much needed changing."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="checked_jobs")
    premeasure = models.ForeignKey(Premeasure, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name="checked_jobs", help_text="The drawing it was pre-measured from.")
    upload = models.ForeignKey(Upload, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
                               help_text="Empty once the file has been cleaned up; the figures are kept.")
    title = models.CharField(max_length=200, help_text="The floor title inside the Callidus job.")
    rooms = models.PositiveIntegerField(default=0)
    area_m2 = models.FloatField(default=0)
    comparison = models.JSONField(null=True, blank=True)
    current = models.BooleanField(default=True, help_text="Replaced when a newer version of the same job is uploaded.")
    uploaded_by = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self) -> str:
        return f"{self.project.code} {self.title}"


class SystemSettings(models.Model):
    """Company-wide settings kept in the database: who is alerted, and how long files are kept."""

    alert_emails = models.TextField(
        "send alerts to", blank=True,
        help_text="One email address per line. Empty: every user who can change settings and has an email.",
    )
    working_files_days = models.PositiveIntegerField(
        "keep working files for (days)", default=60,
        help_text="Close-ups, change pictures and the saved reading of each drawing. They are made again if needed "
        "(reading the drawing again takes a minute or two).",
    )
    replaced_days = models.PositiveIntegerField(
        "remove replaced revisions after (days)", null=True, blank=True,
        help_text="Drawings replaced by a later revision, with their files. Empty: keep them.",
    )
    tender_months = models.PositiveIntegerField(
        "remove the files of tenders inactive for (months)", null=True, blank=True,
        help_text="Drawings, Callidus files and uploads of tenders with no activity for this long. The tender, its "
        "products and its figures stay. Tenders marked to keep are left alone. Empty: never.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "system settings"
        verbose_name_plural = "system settings"

    @classmethod
    def get(cls) -> "SystemSettings":
        return cls.objects.first() or cls.objects.create()


class BackupRun(models.Model):
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    ok = models.BooleanField(default=False)
    target = models.CharField(max_length=300, blank=True)
    database_bytes = models.BigIntegerField(default=0)
    files_sent = models.PositiveIntegerField(default=0)
    bytes_sent = models.BigIntegerField(default=0)
    files_total = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at"]


class Alert(models.Model):
    """A problem the administrators were told about, so it is sent once (and
    again every 12 hours while it lasts), and its end is reported."""

    key = models.CharField(max_length=200, unique=True)
    subject = models.CharField(max_length=300)
    body = models.TextField(blank=True)
    first_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-first_at"]


class DeletionLog(models.Model):
    what = models.CharField(max_length=300)
    reason = models.CharField(max_length=100, help_text="by hand, or the retention rule that removed it")
    bytes_freed = models.BigIntegerField(default=0)
    by = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-at"]
