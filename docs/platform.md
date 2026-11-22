# Using the platform

How an estimator turns finishes drawings into pre-measured Callidus jobs, and the one-off set-up an administrator does first. Everything is in the site itself: Projects, Products and (for administrators) Settings. The Django admin at /admin/ is only a technical back door.

## Settings (administrators, once)

- **Callidus template.** A Crean Callidus job (`.est`) and a background (`.CalBack`) with one sheet. Cashel's pair will do. New files are built on the job when a project has no Callidus job of its own, and new products copy Callidus's roll settings from its products. It needs at least one sheet product and one coved skirting.
- **Measuring rules.** How Crean measures: room edge, glazing, doorways, smallest opening deducted, furniture, stairs, room splits, coving notes, lifts, floors on two sheets, and wastage by product type. Rules marked "used now" already change the output (smallest opening, wastage). The others are recorded and applied as they are built. A project can set its own rules; otherwise it follows these.
- **Users.** Add each estimator. "Can change settings" gives access to this area.

## Products (everyone)

Crean's product list, shared by every project. Each product has its name as it appears in Callidus, type (sheet, tile, carpet tile, matwell, skirting, trim), supplier, product number, colour, size, wastage % and whether it is ordered in whole rolls or boxes. Products enter the list three ways: added by hand, added from a project's products ("Not in the list? Add a new product"), or imported with a Callidus job.

When a product is not already in the Callidus job a file is built on, the platform adds it to the Callidus file, copying Callidus's own roll settings from a product of the same type in the template. Tile and carpet tile products cannot be added this way yet: they must be in the Callidus template or an imported job.

## For each project

1. **Add the project.** You go straight to its products.
2. **Products for this project.** One row per finish code on the drawings, with the product it uses. Fill it in from a drawing's legend in one click (products in the list that clearly match are chosen for you), choose from the list, or add new products. Importing a Callidus job adds its products to the list, matches them to finish codes by product number or colour name (not by the contractor's own codes), and makes that job the one the project's files are built on.
3. **Measuring rules** (optional): set them for this project if it differs from the company rules.
4. **Add finishes drawings,** one or many at a time. A PDF with several pages is fine: each page with a scale and the word "finish" is read, and other pages are skipped. Drawings are read one after another in the background (one to three minutes each). Tick "Write the Callidus files automatically" and every drawing whose finishes all have a product is written without waiting; the rest wait for review. Tick "Email me" for an email when the whole batch is done.
5. **Review a drawing** where needed: the picture of the rooms found, each legend finish with its product (change one for this drawing only), and the areas with their status and reasons. Write, then download the zip.

The zip holds the `.est`, its `.CalBack`, a review file (every area, its status and reasons), a quantities file (net quantity per product, wastage %, quantity with wastage) and a READ ME. Unzip to a local folder (Callidus cannot open files on the Z: drive). Rooms named "CHECK - " need a look. No room has a layout yet: select it and press F9.

## What the software will not do yet

- Guess. A finish with no product is left out of Callidus and listed in the review file.
- Measure finishes drawn only in black linework (often matwells). They are listed under "Measure by hand".
- Cut openings out of a Callidus room. Rooms with an opening or another finish inside are marked "CHECK - " with the area to cut out.
- Add new tile products to Callidus.
- Apply the measuring rules not marked "used now".

## Running it

The web app queues the work; the worker (`web/worker.py`) reads drawings and writes files, one at a time, with 30 minutes allowed per job. For the batch email, set `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL` and `SITE_URL` in `.env` (any SMTP service; leave `EMAIL_HOST` empty to turn email off). For local use without Redis, set `JOBS_INLINE=1` and the work runs inside the request.
