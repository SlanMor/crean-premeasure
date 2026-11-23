# Using the platform

How an estimator turns finishes drawings into pre-measured Callidus jobs, and the one-off set-up an administrator does first. Everything is in the site itself: Tenders, Products and (for administrators) Settings. The Django admin at /admin/ is only a technical back door.

## Settings (administrators, once)

- **Callidus template.** A Crean Callidus job (`.est`) and a background (`.CalBack`) with one sheet. Cashel's pair will do. New files are built on the job when a project has no Callidus job of its own, and new products copy Callidus's roll settings from its products. It needs at least one sheet product and one coved skirting.
- **Measuring rules.** How Crean measures: room edge, glazing, doorways, smallest opening deducted, furniture, stairs, room splits, coving notes, lifts, floors on two sheets, and wastage by product type. Rules marked "used now" already change the output (smallest opening, wastage). The others are recorded and applied as they are built. A project can set its own rules; otherwise it follows these.
- **Users.** Add each estimator. "Can change settings" gives access to this area.
- **Clients and contractors.** Clients, main contractors and architects, with a contact and email. They are picked from a list when a tender is set up, and a new one typed in there is added here. The architect's email fills in the queries email.
- **Rooms marked wrong.** Every room an estimator said was wrong while checking, with their note: where the reading needs work.
- **System.** Backups (each night's result, "Back up now"), problems now, who alerts go to (and a test alert), space used by tender, how long files are kept (working files, replaced revisions, inactive tenders), what the next clean-up will remove, and recent deletions. Setting up backups and restoring: `docs/deploy.md`.

## Products (everyone)

Crean's product list, shared by every project. Each product has its name as it appears in Callidus, type (sheet, tile, carpet tile, matwell, skirting, trim), supplier, product number, colour, size, wastage % and whether it is ordered in whole rolls or boxes. Products enter the list three ways: added by hand, added from a project's products ("Not in the list? Add a new product"), or imported with a Callidus job.

When a product is not already in the Callidus job a file is built on, the platform adds it to the Callidus file, copying Callidus's own roll settings from a product of the same type in the template. Tile and carpet tile products cannot be added this way yet: they must be in the Callidus template or an imported job.

## For each tender

**New tender** (top right) walks through five steps. Any step can be revisited, and a tender left half set up shows "Setting up" in the list.

1. **Tender details.** Project ID and name, then the client, main contractor and architect from the list (or "Add a new..." to type one in), site and due date.
2. **Tender questions.** The measuring rules, starting from the company's. Change only what this tender does differently; they are saved as the tender's own only if something differs.
3. **Drawings.** Drop in the whole pack. In a PDF with several pages, only pages with a scale and the word "finish" are read. Reading starts at once and carries on in the background (one to three minutes a drawing).
4. **Products.** Every finish code on the tender's drawings, once, with what each legend says, how many drawings use it and the area found. Products are suggested where one in the list clearly matches, or where an earlier tender with the same architect (or client) gave the same code with the same legend text. A code that means different things on different drawings is marked "differs". Pick the tender's products from the list too; they are shown first in every product choice.
5. **Run.** Every drawing whose finishes all have a product is written to Callidus, and the tender's to-do list takes over.

**The tender page** leads with its to-do list: drawings that need a scale or sheet size, codes with no product, rooms to check, drawings ready to write (or written before the latest changes), each with one button. Below it: queries for the architect and the tender's quantities.

**Checking rooms.** "Check rooms" shows one room at a time, biggest first, zoomed on the drawing with its outline. Press 1 (or the green button) if it is right: it is no longer marked "CHECK - ". Press 2 if it is wrong, with a note if you like: it is left out of Callidus and listed to measure by hand. S skips. Reading a drawing again numbers its rooms afresh, so its checks are cleared.

**Queries for the architect.** Gathered from every drawing: scans instead of exports, codes not legible on a scan, codes on the plan but not in the legend, coloured areas the legend does not explain, finishes shown by hatching only, and codes described differently on different drawings. Untick any, then "Open in my email" or copy the message.

**Tender quantities.** One spreadsheet: each product's net quantity, how much of it is still to check, wastage and quantity with wastage, split by drawing; every room with its state; and everything still to measure by hand.

**Back from Callidus.** Once the estimator has checked and finished a drawing's job in Callidus, upload the `.est` back (on the drawing page, or several at once on the tender's "Back from Callidus" page; each is matched to its drawing by its name). Uploading a newer version replaces the old one. From these:
- **Crean's measure spreadsheet**: the Callidus room report as Crean lays it out (title, material name, room name, net m2, skirting lm), a subtotal under each product, a grand total, a summary by type of product, and a blank Stairs sheet for the stairs measured by hand.
- **What changed**: each checked job against its pre-measure, room by room (paired by where they are, so renamed rooms still pair): unchanged, outline changed, product changed, split or joined differently, added or removed. "Floor agreed" is the share of the final floor inside the pre-measured outlines; "right first time" is the share in rooms kept as they were.
- **Accuracy on real tenders** (Settings): the same across every tender, split between architects' PDF exports and scans.

**New revisions.** Upload a new revision like any drawing. It is recognised by its file name with the revision left off ("A-110 P2" replaces "A-110 P1"; "Rev C", "_C02" and "(T1)" are read too, but a lone letter such as "part_B" is part of the name). Once read, it is compared with the revision it replaces, room by room, allowing for the plan having moved on the sheet. Rooms that have not changed keep their room checks, and products chosen on the old drawing carry over. Changed and new rooms join "Check rooms". "What changed" shows them on the drawing (added in green, removed in red, changed in orange), the change in area by finish, and a note if the old revision's Callidus job had already come back checked. If a file is named like a revision but most of its rooms differ, it is not linked and the to-do list asks. Any drawing can be linked to the one it replaces, or unlinked, at the bottom of its page. Replaced drawings stay for the record and drop out of the tender's lists and quantities.

**Deleting.** Tick drawings on the tender page and "Delete the ticked drawings", or "Delete this drawing" at the foot of a drawing's page. The confirmation shows the space freed and what else changes: a PDF with other pages still in use stays; the revision a drawing replaced becomes current again; a checked Callidus job stays in the measure spreadsheet. Administrators can also delete a whole tender, or only its files (keeping the tender, its products and its figures), by typing its project ID. Every deletion is listed in Settings, System. Deleted files stay in the backup for 30 days.

**A single drawing** can still be reviewed on its own page: the picture of the rooms found, each legend finish with its product (change one for this drawing only), and the areas with their status. Its "Next" bar says what is left. A product chosen on a drawing for a code the tender has no product for is added to the tender's products. A drawing always follows the tender's products as they are now, except where a different product was chosen on it; if products or room checks change after the files were written, it says to write again.

**Scanned drawings.** A PDF that is only a picture of the drawing (scanned, or saved as an image) is measured from its colours instead. It needs the sheet size (A0, A1...) and the scale. Both are read from the title block when they are printed there; otherwise the drawing stops and asks for them (or give the sheet size when uploading). Every room from a picture is marked "CHECK - ", room names are often missing, and the review page says where the sheet size and scale came from. Areas on simulated scans came within 1 to 3% of the vector reading for most rooms. The architect's own PDF export is exact: ask for it whenever you can.

The zip holds the `.est`, its `.CalBack`, a review file (every area, its status and reasons), a quantities file (net quantity per product, wastage %, quantity with wastage) and a READ ME. Unzip to a local folder (Callidus cannot open files on the Z: drive). Rooms named "CHECK - " need a look. No room has a layout yet: select it and press F9.

## What the software will not do yet

- Guess. A finish with no product is left out of Callidus and listed in the review file.
- Measure finishes drawn only in black linework (often matwells), or, on a scanned drawing, only in hatching. They are listed under "Measure by hand".
- Match plan colours that are lighter tints of the legend colours (some scanned drawings). They are listed under "Colours on the plan that are not in the legend" with their area.
- Cut openings out of a Callidus room. Rooms with an opening or another finish inside are marked "CHECK - " with the area to cut out.
- Add new tile products to Callidus.
- Apply the measuring rules not marked "used now".

## Running it

The web app queues the work; the worker (`web/worker.py`) reads drawings and writes files, one at a time, with 30 minutes allowed per job. For the batch email, set `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL` and `SITE_URL` in `.env` (any SMTP service; leave `EMAIL_HOST` empty to turn email off). For local use without Redis, set `JOBS_INLINE=1` and the work runs inside the request.
