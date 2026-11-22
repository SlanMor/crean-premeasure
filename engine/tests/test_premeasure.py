"""The platform's one-call pipeline, on the Cashel P2 drawing (needs fixtures)."""

import csv
import os
from pathlib import Path

import pytest

ROOT = os.environ.get("PREMEASURE_FIXTURES")


@pytest.fixture(scope="module")
def cashel():
    if not ROOT:
        pytest.skip("PREMEASURE_FIXTURES not set")
    d = Path(ROOT) / "cashel"
    pdf, ests, cbs = d / "finishes-P2.pdf", sorted(d.glob("*.est")), sorted(d.glob("*.CalBack"))
    if not (pdf.exists() and ests and cbs):
        pytest.skip("Cashel fixtures missing")
    from premeasure_engine.premeasure import analyse

    return analyse(pdf, ests[0]), ests[0], cbs[0]


def test_analysis_lists_every_finish_with_a_safe_suggestion(cashel):
    from premeasure_engine.premeasure import summary

    a, _, _ = cashel
    s = summary(a)
    by_code = {f["code"]: f for f in s["finishes"]}
    assert by_code["FF13"]["suggestion"] == "FF8 - Forbo Concrete Neptune 3756"
    assert by_code["FF7"]["suggestion"] is None  # Blue Ice is not in Crean's file: never guessed
    assert by_code["FF13"]["amount"] == pytest.approx(120.14, abs=0.5)
    assert s["manual_finishes"] == ["FF5"]
    assert "NONE" not in s["materials"]
    assert len(s["rooms"]) >= 40


def test_writes_a_job_callidus_can_read(cashel, tmp_path):
    from premeasure_engine.callidus import read_calback, read_est
    from premeasure_engine.premeasure import render_preview, write_outputs

    a, est, cb = cashel
    floors = {f.code: f.suggestion for f in a.finishes if f.kind == "floor" and f.suggestion}
    sk1 = "FF6 - Forbo Marmoleum Fresco 3.5mm 19db colour silver shadow 386035"  # any product will do here
    out = write_outputs(a, est, cb, floors, {"SK1": sk1}, tmp_path, "CASHEL-test")

    job = read_est(out.est)
    assert len(job.rooms) == out.written > 20
    from premeasure_engine.premeasure import outline_area

    written = [r for r in a.rooms if r.finish and r.finish.code in floors]
    assert sum(r.area for r in job.rooms) == pytest.approx(sum(outline_area(r) for r in written), abs=0.05)
    assert out.written_m2 == pytest.approx(sum(r.area for r in written), abs=0.05)
    # a room with an opening is never passed as verified, because Callidus would count the opening
    assert all(r.status != "Verified" for r in written if r.geom.interiors)
    assert {r.material.name for r in job.rooms} <= set(floors.values())
    assert len(read_calback(out.calback).sheets) == 1

    rows = list(csv.DictReader(out.review.open(encoding="utf-8-sig")))
    assert sum(r["In Callidus file"] == "yes" for r in rows) == out.written
    assert any(r["Status"] == "Measure by hand" and r["Drawing finish"] == "FF5" for r in rows)
    assert "Z: drive" in out.readme.read_text()

    png = render_preview(a, tmp_path / "p.png", highlight=set(floors))
    assert png.stat().st_size > 10_000


def test_refuses_a_product_not_in_the_starter_file(cashel, tmp_path):
    from premeasure_engine.premeasure import write_outputs

    a, est, cb = cashel
    with pytest.raises(ValueError, match="not in the starter file"):
        write_outputs(a, est, cb, {"FF13": "Made-up product"}, {}, tmp_path, "x")


def test_quantities_file_applies_wastage(cashel, tmp_path):
    from premeasure_engine.premeasure import write_outputs

    a, est, cb = cashel
    neptune = "FF8 - Forbo Concrete Neptune 3756"
    out = write_outputs(a, est, cb, {"FF13": neptune}, {}, tmp_path, "q", wastage={neptune: 10})
    (row,) = list(csv.DictReader(out.quantities.open(encoding="utf-8-sig")))
    assert row["Product"] == neptune and row["Unit"] == "m2"
    assert float(row["Net quantity"]) == pytest.approx(120.14, abs=0.5)
    assert float(row["Quantity with wastage"]) == pytest.approx(float(row["Net quantity"]) * 1.1, abs=0.01)


def test_plan_pages_skips_pages_that_are_not_plans(tmp_path):
    if not ROOT:
        pytest.skip("PREMEASURE_FIXTURES not set")
    import pypdfium2 as pdfium

    from premeasure_engine.premeasure import plan_pages

    doc = pdfium.PdfDocument.new()
    doc.new_page(595, 842)
    doc.import_pages(pdfium.PdfDocument(str(Path(ROOT) / "cashel" / "finishes-P2.pdf")))
    doc.save(tmp_path / "two.pdf")
    assert plan_pages(tmp_path / "two.pdf") == [1]
