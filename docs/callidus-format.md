# Callidus file format: what we know

Decoded in November 2026 from nine floor files across four Crean jobs. Reading is proven (Crean's spreadsheets reproduce exactly, apart from rows explained by later edits). Editing is proven: Callidus opens our edited files and reads renames, finish changes and moved outlines as intended (tests T1 to T4). Writing a whole room from scratch is test T5.

## .est (estimate)

A Microsoft MFC `CArchive` binary.

- **Header:** byte 8 is the length of the floor title, followed by the title (for example `Ground Floor`).
- **Class declarations:** `FF FF 01 00`, a 2-byte name length, then the class name. Classes seen: `Material`, `ProductTile`, `ProductOLDSheet`, `ProductNet`, `Room`, `TileZone`, `OldSheetZone`, `NetZone`, `FullTile`, `CutTile`, `RollCut`, `CuttingFrame`, `rfCutData`, `rfRoomCut`, `rfStockRoll`, `FPoly`.
- **Object references:** MFC numbers every class and object in the order they are first written. Rooms refer to their material by this number. Each material contributes a material object and a product object, plus a class declaration the first time a product class appears.
- **Materials:** an XML block `<matName><name>…</name><CallidusID>…</CallidusID><TPPid/></matName>`. Tile products also carry a `<tileGrid>` XML block with tile width and length in metres.
- **Rooms:**
  - uint32 vertex count, then one 30-byte record per vertex: float32 x, float32 y (metres), 18 bytes of edge data, uint32 `1000`.
  - Then uint32 `1000`, a length-prefixed XML block `<roomName><name>…</name><CallidusID>…</CallidusID><TPPid/></roomName>`, then a uint16 material reference.
  - A zone object follows (`TileZone`, `OldSheetZone` or `NetZone`) with its own outline, then the calculated layout (tiles, cuts, roll cuts, cutting frames).
- **Room list:** a uint16 count of top-level rooms, then the Room objects one after another (Cashel: 53). A room can hold sub-rooms (Cashel has three `NetZone` rooms inside "GF Circulation West Design 1"); our reader lists those as rooms too, which is why it finds 56.
- **MFC object numbers:** each new class and each new object takes the next number. A room with no layout is exactly two objects (the Room and its zone). A room with a layout adds one object per layout piece, and layouts refer back to pieces in other rooms and to a roll list at the end of the file. That is why removing a layout from the middle of a file means renumbering everything after it, and why we replace no-layout rooms instead (see T5).
- **Room block after the name** (offsets from the end of the name string; holds for 479 of 483 rooms in nine files):
  - +0: uint16 material reference, then one byte.
  - +3: point record (x, y, 18 bytes, uint32 1000), the layout origin. The zone repeats it.
  - +33: 8 zero bytes.
  - +41: float32 layout direction in radians. The zone repeats it.
  - +45: float32, usually 0 (meaning not known).
  - +54: point record, the label position (confirmed by T3: moving it moved the label).
  - +84: uint32 1, uint32 1, then uint32 reference edge index (always less than the vertex count).
  - +96: uint16, always 0.
  - +98: uint16 number of sub-rooms, followed by the sub-room objects.
  - +100 (after any sub-rooms): 14 zero bytes, then at +114 a point record at or near the first vertex.
  - +144: 92 bytes that are the same in every room except two flag bytes (+222 and +234; meaning not known).
  - +0xec: the zone object tag; +0xee the zone outline. In 139 rooms the zone outline matches the room; in the rest it differs, usually because the room has been laid out.
- **A room with no layout** (written by `callidus/room_writer.py`, byte-exact on 17 rooms across the nine files):
  - Room object, then the zone: outline, a null parent (uint16 0), the origin point, the direction, uint32 1000.
  - Then uint16 zone object number (the zone refers to itself), uint16 material, three empty lists (`e8030000 00000000`, `e8030000 00000000`, `e8030000`), uint16 0 for no layout pieces, and uint32 1000.
  - The zone's edge data either copies the room's edges or is all zero; quantities come from the room's edges.
- **Edge data (18 bytes per edge, from that vertex to the next):**
  - byte 0 = 1: edge is open (no wall).
  - byte 8 = 2: another edge flag, excluded from room-level skirting; meaning not confirmed (possibly doorway).
  - byte 12: material number of an edge product (for example 125mm coved skirting, matwell frame), 0 if none.
  - bytes 14 to 17: float32 0.2 when an edge product is set (meaning not yet known).
- **Coordinates:** sheet position multiplied by drawing scale, in metres. The bottom edge of the sheet is y = 0 and the top edge is y = minus the sheet height (the CalBack record gives y-top -59.40, y-bottom 0 for Cashel), so y gets more negative further up the sheet. Confirmed by overlaying our room outlines on Callidus screenshots.
- **Net area:** room area minus any room inside it.
- **Skirting:** either per edge (byte 12), or room-level (every edge with all 18 bytes zero), depending on how the job was set up.

- **Tail section** (after the room list, found by the bytes `00 00 1a 00 00 00 04 Item`, identical in layout in all nine files):
  - uint16 0, uint32 26 and 26 strings "Item", fixed fields, a uint16 object reference (Cashel and Limerick: object 2, the NONE material), floats.
  - At 265 bytes from the start: one list per finish plus four (uint16 count, then uint16 object references to stock rolls), then uint32 1000 at the end of the file. A file with no layouts has every list empty.
- **Whole job** (written by `callidus/job_writer.py`): header and finishes copied from a Crean file, a new room list, and the tail with empty lists. The first room declares the `Room` and `OldSheetZone` classes; after that every number follows in order.

## .CalBack (background)

- uint32 at offset 4: number of sheet images.
- Before each image, a 0x2c-byte record: float32 x-left, y-top, x-right, y-bottom at offsets 8, 12, 16, 20; uint32 image length at 0x28.
- Each image is a 1-bit TIFF, CCITT Group 4, at 300 to 600 dpi. In all eleven samples it is big-endian, WhiteIsZero, with 3 or 6 rows per strip. A uint32 1000 follows the last image.
- Cashel's background is the architect's PDF at exactly 300 dpi (A1 gives 9933 by 7016 pixels, rounded to the nearest pixel), and our render of the PDF lines up with it to the pixel. `callidus/calback_writer.py` renders a PDF page, writes the TIFF in the same form, and puts it in a copy of a template .CalBack.
- Several sheets can sit in one floor file (Shellybanks ground and first floor have two each).
- A float at offset 0x2c appears to relate to scale calibration (values seen: 0.9986, 1.0, 1.9975, 2.0, 4.0001); its exact meaning is not confirmed. Limerick sheet sizes differ from nominal A0 by 0.12 to 0.14%, which suggests the estimator calibrated them.

## Test files sent to Crean (November 2026)

Built from the Cashel "Revised measure" file with `scripts/make_callidus_test_set.py`:

| File | Change | What it tells us |
| --- | --- | --- |
| T0-control | none | a copied file opens |
| T1-rename | G19 renamed | text edits that change length are accepted |
| T2-finish | G25 from FF6 to FF9 | changing the material reference is enough |
| T3-move-all | G25, its zone and layout points moved 1 m | whether Callidus redoes the layout itself |
| T4-move-outline | G25 outline only moved 1 m | whether the room or the zone outline drives area |
| T5-new-room | "Half landing 1 main stair" replaced by "T5 TEST ROOM", a 4 m by 3 m FF6 rectangle with coved skirting, written from scratch with no layout | whether Callidus accepts and can lay out a room we write |

Results so far (22 November 2026, from screenshots; the test rooms were not clicked, so these come from comparing the screenshots pixel by pixel):

- All five files open in Callidus with no errors.
- Callidus's own figures match our reader: F06 net 57.14 m² and perimeter 31.36 m (ours 57.139, 31.356); FF8 net total 333.02 m² (ours 333.023); Circulation South East Entrance net 14.22 m² and perimeter 15.55 m (ours 14.218, 15.554); FF12 net total 193.56 m² (ours 193.561).
- T1: confirmed. G19's label reads "G19 - TEST RENAME" on screen. G25 in T1 (unchanged) shows net 3.33 m², perimeter 9.00 m, FF6, and FF6 net total 184.51 m² (ours 3.333, 8.999, 184.510).
- T2: confirmed. G25 shows FF9 Marmoleum Fresco Silver Shadow 3860, and the FF9 net total is 120.09 m² (ours 120.093, which is 116.760 plus G25's 3.333). Rewriting the uint16 material reference is enough to move a room to another finish. Whether G25 kept its layout after the change is not yet checked.
- T3: G25's label and its pointer moved about 1 m right, so the +3 label point is confirmed.
- T4: G25's label did not move, as intended.
- T3 and T4: G25's outline is drawn 1 m to the right, as written, with net 3.33 m² and perimeter 9.00 m in both. Its layout (the hatched cut pieces) stayed at the old position in both files, and the FF6 gross (208.57 m²) and waste (13.04%) did not change. Callidus does not recalculate a layout when a file is opened: layout pieces carry their own coordinates, and moving the zone and layout points does not move them. A writer that changes a room's shape or position must therefore remove that room's layout, never leave the old one in place.
- Callidus already holds rooms with no layout: in this file the FF8 rooms have short room blocks (about 0.5 to 1.4 KB, against 2.3 to 7.2 KB for FF6 rooms with layouts), and Callidus shows their net area with gross 0.00 m². These blocks are the template for rooms we write.
- FF8 shows gross 0.00 m² and no waste figure, so Crean already leaves some materials without a layout.

Optional: re-do G25's layout from the Layout menu in T3, to confirm a stale layout is replaced cleanly.

T5 was built instead of removing G25's layout: G25's 12 layout objects are referred to from the roll list at the end of the file, and taking them out would renumber every object after G25. Replacing a no-layout room keeps every object number the same. Expected in Callidus: T5 TEST ROOM net 12.00 m², perimeter 14.00 m, FF6; FF6 net total 196.51 m² (was 184.51); FF8 net total 331.47 m² (was 333.02).

T5 result (22 November 2026): passed. The file opens with no errors. T5 TEST ROOM shows net 12.00 m², perimeter 14.00 m, FF6, drawn with its skirting edges in blue like Callidus's own skirted rooms, and the FF6 net total is 196.51 m². Saving in Callidus, closing and reopening keeps the room. FF6 gross stayed at 208.57 m², so the room had not been laid out in that screenshot; laying it out from the Layout menu is still to be confirmed.

T6 (22 November 2026): the whole Cashel job written by our writer: Crean's finishes, all 51 sheet-finish rooms, no layouts. T6a uses the original background, T6b one rendered from the PDF. Left out: two FF1 carpet tile rooms (tile zones not written yet) and the three NetZone cut-outs inside "GF Circulation West Design 1", which is written whole. Expected net totals as the original except FF8: 342.92 m² (333.02 plus the 9.90 m² of cut-outs). Results: awaiting Crean.

Practical note: Callidus would not open any file from the estimator's Z: drive (a folder whose name it shows as "Callidus?"), giving "Cannot open file". The same file opened from a local folder. Delivered files need to be saved to a local folder with a plain name before opening.

## Open (needed for writing)

- Remaining fields in room and zone records.
- Whether Callidus recalculates layouts when a file has rooms and zones but no layout objects.
- Whether files differ between Callidus versions.
- Four test files from Crean will settle most of this: a blank job with a drawing loaded; one rectangular room in a tile; the same room in a sheet product; the same room moved a known distance.
