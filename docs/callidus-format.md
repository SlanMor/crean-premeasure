# Callidus file format: what we know

Decoded in November 2026 from nine floor files across four Crean jobs. Reading is proven (Crean's spreadsheets reproduce exactly, apart from rows explained by later edits). Writing is not yet proven: see "Open" at the end.

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
- **Room block after the name** (offsets from the end of the name string; holds for 479 of 483 rooms in nine files):
  - +0: uint16 material reference, then one byte.
  - +3: point record (x, y, 18 bytes, uint32 1000), the label position.
  - +41: float32 layout direction in radians (seen 3.1416 and 4.7124).
  - +54: point record, the layout start point.
  - +114: point record, a further reference point (meaning not confirmed).
  - +0xee: the zone outline in 139 rooms, identical to the room outline. In the rest the zone sits elsewhere or differs from the room; the patcher refuses to move those.
- **Edge data (18 bytes per edge, from that vertex to the next):**
  - byte 0 = 1: edge is open (no wall).
  - byte 8 = 2: another edge flag, excluded from room-level skirting; meaning not confirmed (possibly doorway).
  - byte 12: material number of an edge product (for example 125mm coved skirting, matwell frame), 0 if none.
  - bytes 14 to 17: float32 0.2 when an edge product is set (meaning not yet known).
- **Coordinates:** sheet position multiplied by drawing scale, in metres. Y runs negative from the top of the sheet.
- **Net area:** room area minus any room inside it.
- **Skirting:** either per edge (byte 12), or room-level (every edge with all 18 bytes zero), depending on how the job was set up.

## .CalBack (background)

- uint32 at offset 4: number of sheet images.
- Before each image, a 0x2c-byte record: float32 x-left, y-top, x-right, y-bottom at offsets 8, 12, 16, 20; uint32 image length at 0x28.
- Each image is a 1-bit TIFF, CCITT Group 4, at 300 to 600 dpi.
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

Results: awaiting Crean.

## Open (needed for writing)

- Remaining fields in room and zone records.
- Whether Callidus recalculates layouts when a file has rooms and zones but no layout objects.
- Whether files differ between Callidus versions.
- Four test files from Crean will settle most of this: a blank job with a drawing loaded; one rectangular room in a tile; the same room in a sheet product; the same room moved a known distance.
