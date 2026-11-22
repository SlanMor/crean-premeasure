# Using the platform

How an estimator turns a finishes drawing into a pre-measured Callidus job, and the one-off set-up an administrator does first.

## One-off set-up (administrator)

1. **Accounts.** In Admin, Users, add a user for each estimator (username and a password of 12 characters or more). They do not need admin access.
2. **House Callidus template.** In Admin, Callidus templates, add a Crean Callidus job (`.est`) and a `.CalBack` with one sheet (Cashel's pair will do). The job is what new files are built on when a project has no Callidus job of its own, and its products are the ones new products copy their roll and waste settings from, so it should have at least one sheet product and one coved skirting. The `.CalBack` is only a container; the drawing itself comes from the PDF.

## For each project: Project setup

1. Sign in and add the project (code and name are enough). You go straight to Project setup.
2. **Set the tender's products.** One row per product: the finish code used on the drawings (F1-A, FF7), the product name as it should appear in Callidus, its colour, and which existing product its settings copy. Three ways to fill the list, which can be mixed:
   - type the products in;
   - add a finishes drawing first, then "Add its finishes" to fill the list from its legend, and correct the names to the tender's products;
   - import a Callidus job that already has the products set up. Its products are used exactly as they are, and it becomes the job this project's files are built on. A finish code is only filled in where a drawing's legend clearly matches (by product number or colour name), because a contractor's own numbering (Crean's FF8) need not match the architect's.
3. A product already in the Callidus job is used as it is. Any other product is added to the Callidus file by the platform.

## For each drawing

1. **Add the finishes drawing**: the PDF, the page with the plan, the level, and which Callidus job's products to use. Press "Read the drawing". It takes one to three minutes; the page updates by itself.
2. **Check the products.** Every finish on the legend is listed with the area found and its product from the project setup, matched by finish code. Finishes the setup does not have are listed, with a button to add them. Change any that are wrong, or choose "Leave out". For skirting, choose a product only for the types you want on the room edges (usually coved skirting).
3. **Look at the drawing and the areas.** Green: found and verified. Amber: found but needs a check (the reason is in the list). Grey: no product chosen.
4. **Write Callidus files**, then download the zip. Unzip to a local folder (Callidus cannot open files on the Z: drive). It holds the `.est`, its `.CalBack`, a review spreadsheet and a READ ME.
5. In Callidus, rooms named "CHECK - " need a look. No room has a layout yet: select a room and press F9.

Every drawing on the project uses the same setup, so later drawings need no product choices unless they bring new finishes.

## What the software will not do

- Guess. A finish with no clear product match is left out until the estimator chooses one.
- Measure finishes drawn only in black linework (often matwells). They are listed under "Measure by hand".
- Cut openings out of a Callidus room yet. Rooms with an opening or another finish inside are marked "CHECK - " and the reason says how much to cut out.
- Read more than one page per drawing in one go. A floor on two sheets (Shellybanks) is two drawings, so two Callidus files.

## Running it

The web app queues the work; the worker (`web/worker.py`) reads drawings and writes files. A large A0 drawing takes up to about three minutes to read; the queue allows 30 minutes per job. For local use without Redis, set `JOBS_INLINE=1` and the work runs inside the request.
