"""New revisions of a drawing: found by the drawing's name, compared with the
revision they replace once read, and the estimator's work carried over for
every room that has not changed."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from .models import Premeasure, Project, RoomCheck

# a revision at the end of a file name: "Rev B", "rev.C", "_P2", "-C02", "(T1)".
# A lone letter ("part_B") is not taken as a revision: it is usually part of the name.
REVISION = re.compile(
    r"[\s_\-.]*[(\[]?\s*(?:rev(?:ision)?[\s_.\-]*(?P<rev>[A-Z0-9]{1,3})|(?P<code>[PCT]\d{1,2}))\s*[)\]]?$",
    re.IGNORECASE,
)


def split_revision(filename: str) -> tuple[str, str]:
    """(the drawing's own name, its revision) from a file name."""
    stem = Path(filename).stem.strip()
    m = REVISION.search(stem)
    if not m or m.start() == 0:
        return stem, ""
    return stem[: m.start()].strip(" _-."), (m.group("rev") or m.group("code")).upper()


def drawing_key(filename: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", split_revision(filename)[0].lower())


def prior_revision(project: Project, filename: str, page: int, exclude=None) -> Premeasure | None:
    """The current drawing this file looks like a new revision of: the same
    drawing name (revision left off) and page."""
    key = drawing_key(filename)
    if not key:
        return None
    candidates = project.current_drawings().filter(page=page).select_related("drawing").order_by("-created_at")
    if exclude is not None:
        candidates = candidates.exclude(pk__in=exclude)
    for pm in candidates:
        # the drawing number is the file's own name (stored names can gain a suffix)
        if drawing_key(pm.drawing.drawing_number or pm.drawing.filename) == key:
            return pm
    return None


def compare(pm: Premeasure) -> None:
    """After a drawing is read: compare it with the revision it replaces, carry
    the estimator's work over, or let go of a link made by name that the
    drawing does not bear out."""
    from premeasure_engine.revisions import compare_revisions, render_changes

    from .tasks import _analysis, _save_file

    old = pm.supersedes
    if old is None or not old.summary:
        return
    diff = compare_revisions(_analysis(old), _analysis(pm))
    result = diff.as_dict()
    if pm.revision_link == "auto" and not diff.likely_same_drawing:
        # named like a revision, but most rooms differ: probably another drawing
        pm.supersedes = None
        pm.revision_diff = {"rejected": old.title, "kept": result["kept"]}
        pm.save(update_fields=["supersedes", "revision_diff", "updated_at"])
        return
    with tempfile.TemporaryDirectory() as tmp:
        png = render_changes(_analysis(old), _analysis(pm), diff, Path(tmp) / "changes.png")
        _save_file(pm, "changes", png)
    pm.revision_diff = result
    carried = carry_over(old, pm, result)
    pm.revision_diff["carried_checks"] = carried
    pm.save()


def carry_over(old: Premeasure, new: Premeasure, diff: dict) -> int:
    """Room checks for rooms that have not changed, and products chosen on the
    drawing. Returns the number of room checks carried."""
    unchanged = {r["old_id"]: r["new_id"] for r in diff["rooms"] if r["state"] == "unchanged"}
    n = 0
    for c in old.checks.all():
        if c.room_id in unchanged:
            RoomCheck.objects.update_or_create(
                premeasure=new, room_id=unchanged[c.room_id],
                defaults={"decision": c.decision, "note": c.note, "by": c.by},
            )
            n += 1
    if old.chosen_here and not new.chosen_here:
        new.chosen_here = old.chosen_here
    return n


def changed_rooms(pm: Premeasure) -> set[int]:
    """Rooms of this revision that are new or different from the last one."""
    diff = pm.revision_diff or {}
    return {r["new_id"] for r in diff.get("rooms", []) if r["new_id"] is not None and r["state"] != "unchanged"}


def link(pm: Premeasure, old: Premeasure | None) -> None:
    """The estimator says which drawing this replaces (or none). Compared now
    if both have been read."""
    if pm.supersedes_id and pm.supersedes_id != getattr(old, "pk", None):
        # the earlier link goes, and so does what was carried over
        pm.checks.all().delete()
    pm.supersedes = old
    pm.revision_link = "manual" if old else ""
    pm.revision_diff = None
    if pm.changes:
        pm.changes.delete(save=False)
    pm.save()
    if old is not None and not pm.busy and pm.summary:
        compare(pm)
