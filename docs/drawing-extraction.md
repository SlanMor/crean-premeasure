# Reading finishes drawings

How the engine reads rooms, finishes and skirting from an architect's PDF, and how well it does on the first drawing tried. Code: `engine/src/premeasure_engine/drawing/`.

## Method

1. **Read the page** (`pdf_reader.py`, pdfium). Fills, strokes and text are converted to Callidus sheet metres: x from the left edge, y = 0 at the bottom edge and negative going up. The scale comes from the "1 : N" text on the sheet. Rotated pages (`/Rotate`, Shellybanks) are turned to how they display. Each fill is cut to its clip path: Revit draws many rooms as one large rectangle clipped to the room shape (Shellybanks). Islands drawn the other way round are holes.
2. **Read the legend** (`legend.py`). Two layouts are read. Cashel style: each "FLOOR FINISH TYPE n" or "SKIRTING TYPE n" heading has a swatch beside it. Swatch-column style (Limerick, Shellybanks): a column of three or more aligned swatches, each with its code (F1-A, M50-161) and description beside it. The swatch gives the finish's fill colour and hatch colour. Some finishes have a hatch and no fill (carpet and matting on Cashel). A code used twice (Limerick's M50-150 in three colours) is kept as separate finishes.
3. **Work out what is visible** (`rooms.py`). Later shapes cover earlier ones, so each fill only counts where it can be seen. This is done per 1 m cell, top layer first (about 10 seconds for 12,000 fills).
4. **Finish areas.** A fill colour is a floor finish if it matches a legend swatch (exactly, or within a few RGB units), or if the hatch over it matches a legend hatch (such rooms are marked for review). A hatch shared by three or more finishes says nothing and is not used (Shellybanks draws ten finishes with the same grey hatch). White fills are walls and masking, never floor. Other colours are reported, not measured, with one exception: if every area of that colour carries the same finish tag written like the legend's codes (F1-H beside F1-A to F1-G), the architect has left a finish off the legend, and the area is measured under that code and marked for review.
5. **Skirting and edge strips.** Thin strips, a few centimetres wide, run along the walls. Each strip joins the room it runs along, so rooms reach the wall face. Strip colour gives the skirting type: blue for the legend's blue (SK1, coved), warm brown for its brown (SK2, softwood). Strips are drawn blended with the floor under them, so their exact colour varies; the colour family is what is matched. Each room edge is split wherever skirting starts or stops, and every edge carries its skirting type.
6. **Hatch-only finishes.** Unfilled floor inside the building outline (doors and entrances sealed by closing 1 m gaps) that carries a legend hatch becomes a room of that finish. Black hatch is not used, because walls, doors, stairs and text are black too; finishes drawn only in black are listed for manual measurement.
7. **Room tags.** Codes such as G01 or F06, and the name printed under them, are attached to the room they sit in.
8. **Status.** A room is "Verified" only when nothing was uncertain. Otherwise it is "Needs review", with reasons: finish matched by hatch only, finish written on the drawing but not in the legend, no room tag, an opening left out of the area, stairs (the plan area includes the flights).
9. **Products** (`products.py`). Each legend finish is matched to a Callidus material by the drawing's own finish code when the material name starts with it (F1-E and "F1E - Style Emme Pear 753"), and by shared product codes and colour names (for example "NEPTUNE 3756"). Where one code covers several colours, only the words that tell them apart count, so M50-150 Sparrow is never matched to M50-150 Cinder. This is a suggestion for the estimator to confirm; a finish with no match is never guessed and is left out of the Callidus file.
10. **Callidus file** (`to_callidus.py`). Rooms whose finish has a Callidus material are written with `job_writer`. Rooms needing review are written with "CHECK - " in front of the name. Coved skirting edges carry the skirting material, as Crean's files do.

## Cashel P2 (2005-KLA-00-ZZ-DR-A-110 P2), first run, 22 November 2026

- Legend: 13 floor finishes and 3 skirting types read, with descriptions.
- Rooms: 55 areas found. Of everything Crean measured (1,472 m² excluding cut-outs), 1,445.5 m² (98.2%) is covered. The 26.5 m² not covered is mainly the four matwells (14 m², black hatch only) and a sliver along walls.
- Where Crean traced to the wall face, areas agree to within 0.1 m²: F11 115.95 against 115.94, G11 50.01 against 50.08, G18 20.31 against 20.39, G19 15.09 against 15.09.
- Elsewhere our rooms are larger by about 8 cm all round, because Crean traced the inside of the skirting band drawn on plan while we measure to the wall face (F01: 60.34 against 57.20 for Crean's two rooms). Which rule Crean wants is a question for them.
- Coved skirting (SK1) is found in every wet room Crean measured; lengths are about 8 to 12% above Crean's, for the same reason.
- Product matching: 8 of 13 finishes match a material in Crean's Cashel file. The other five (Blue Ice 3753, Sirius 3754, Taiga 3752, Tundra 3751, Petrol 3358) are not in that file, which fits the findings that P2 changed finishes against what Crean priced.
- Not handled yet: matting (black hatch), openings inside rooms (written as solid rooms and flagged), curves (kept as their points), several pages per PDF.

## Test file T7

`CASHEL-T7-from-pdf.est`: every room whose finish has a Callidus material in the Cashel file (28 rooms, 578 m²), read from the PDF and written by our writer on the background rendered from the same PDF, with coved skirting on the edges where the drawing shows it. The review report lists every room, written or not, with status and reasons. Results: awaiting Crean.

## Limerick Surgical Hub and Shellybanks ETNS, 22 November 2026

Seven floors read with the same code, no per-job settings, and compared with Crean's measured Callidus files. Only finishes that match a material in Crean's file count (epoxy, sealed concrete and tanking are on the legends but not priced by Crean). "Same product" is the area where we and Crean agree on both the floor and the product.

| Floor | Crean m² | Found m² | Same product | Crean only | Ours only |
|---|---|---|---|---|---|
| Limerick GF | 1,303.0 | 1,315.3 | 1,294.9 (99.4%) | 7.0 | 19.3 |
| Limerick FF | 2,481.7 | 2,330.5 | 1,817.4 (73.2%) | 242.7 | 91.5 |
| Limerick 2F | 106.5 | 129.5 | 105.8 (99.4%) | 0.7 | 23.7 |
| Shellybanks B | 566.1 | 542.2 | 541.2 (95.6%) | 24.8 | 0.9 |
| Shellybanks GF | 1,301.6 | 1,293.1 | 1,247.7 (95.9%) | 53.8 | 45.3 |
| Shellybanks 1F | 1,268.9 | 1,302.8 | 1,240.2 (97.7%) | 28.4 | 62.2 |
| Shellybanks 2F | 208.4 | 215.5 | 204.8 (98.3%) | 3.5 | 10.6 |

What the differences are:

- **Matwells** (Shellybanks, 74 m² over three floors) are white with a black frame on the drawing and are not found. They are listed for manual measurement, as on Cashel. Without them, Shellybanks is 98.8 to 99.1% on every floor below the second.
- **Limerick first floor.** The drawing we have shows Operating Theatres 3 and 4 as "shell only" and the recovery cubicles in Cinder with small Leaf accents. Crean's file has both theatres fitted out (242 m²) and the cubicles in Avocado and Sparrow (420 m²). Crean probably priced a different revision or an instruction. The tool follows the drawing it is given; this is a question for the estimator.
- **Stairs** add 15 to 45% to the stair finish (F1-G on Shellybanks, Cinder on Limerick 2F). We measure the stair in plan, flights included; Crean measures landings and half landings. Stair rooms are now marked for review.
- **Room splits.** Crean splits long corridors and class bases into several rooms; we return one area per continuous finish. Totals agree; one-to-one room matches are lower for that reason (Shellybanks GF: 35 of 54 within 10%).
- **Product matching:** 18 to 20 of 21 Shellybanks finishes and 8 of 12 Limerick finishes match a Crean material by code. F1-H (Veneto Midnight) is missing from the Shellybanks legend but written in the rooms; it is found by its tag and matches Crean to 0.04 m².

## Drawings that arrive as pictures, 23 November 2026

A PDF with no linework (scanned, or flattened to an image, like the UCD first floor part B) is read by `drawing/picture.py`, with OpenCV for the image work and Tesseract for the text.

1. The picture is read at its own resolution. Sheet size and scale come from the estimator, else the title block. A sheet size from nowhere else (the PDF page size) is only used if at least three printed room areas confirm it; otherwise the estimator is asked.
2. Legend swatches are found as small closed boxes or single-colour patches stacked in a column; their text is read beside them. Headings become codes (FLOOR FINISH TYPE 9 is FF9). Finishes named on the legend with no swatch found (hatching only) go to "Measure by hand". Codes that break the legend's pattern (P22 among F1-A, F1-B...) are reported as unclear.
3. Each plan pixel takes the nearest legend colour after a small median filter (it takes out stipple and text). A room only starts where the swatch colour itself shows in most pixels (so grey walls or textures that only average out to a finish colour start nothing), and grows back out to the wall face: across a coloured skirting band and the line beside it, but never into a solid wall.
4. Outlines are fitted with straight lines snapped to the building's angles and pushed out a quarter pixel to the middle of the soft edge.
5. Room tags and printed areas are read inside each room (only words Tesseract is sure of). If at least three printed areas agree and differ from the measured ones by more than 3%, every room says to check the sheet size and scale; one room more than 5% out says to check its outline.
6. Every room is marked for review. Coloured plan areas that match no legend colour are reported with their area.

Accuracy against the vector reading of the same drawing, rendered as a picture at 106 dpi (a typical A0 or A1 scan), rooms matched one to one:

| Drawing | Median error | Mean absolute error |
|---|---|---|
| Limerick 2F (A0) | +0.8% | 1.4% |
| Shellybanks 1404 (A1) | +0.9% | 1.5% |
| Cashel P2 (A1) | -2.5% | 2.9% |

Cashel draws its fills under the heavy wall lines, so a picture of it reads a little short; Limerick stops its fills at the line. Hatch-only finishes (Cashel FF5 and FF10) are not measured. On the real UCD scan, the sheet (A0) and scale (1:100) are read from the title block; 690 m2 is measured, and about 1,050 m2 is reported as colours not in the legend, because the plan uses lighter tints of the legend colours, which cannot be tied to one finish without guessing.
