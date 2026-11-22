# Using the platform

How an estimator turns a finishes drawing into a pre-measured Callidus job, and the one-off set-up an administrator does first.

## One-off set-up (administrator)

1. **Accounts.** In Admin, Users, add a user for each estimator (username and a password of 12 characters or more). They do not need admin access.
2. **Background template.** In Admin, Background templates, upload any Crean `.CalBack` that has one sheet (Cashel's will do). It is only used as a container: the drawing itself comes from the PDF. It is needed when a project's own `.CalBack` has several sheets (Shellybanks) or none was uploaded.

## For each project

1. Sign in, add the project (code and name are enough).
2. **Add the Callidus job.** Upload any Callidus `.est` for the project that has its products set up, and its `.CalBack`. Its rooms are not used; only its products are. Without this the drawing cannot be matched to products.

## For each drawing

1. **Add the finishes drawing**: the PDF, the page with the plan, the level, and which Callidus job's products to use. Press "Read the drawing". It takes one to three minutes; the page updates by itself.
2. **Check the products.** Every finish on the legend is listed with the area found and a suggested Callidus product. A suggestion is only made on a clear match of product codes or colour names; otherwise it says "Leave out (not priced)". Change any that are wrong. For skirting, choose a product only for the types you want on the room edges (usually coved skirting).
3. **Look at the drawing and the areas.** Green: found and verified. Amber: found but needs a check (the reason is in the list). Grey: no product chosen.
4. **Write Callidus files**, then download the zip. Unzip to a local folder (Callidus cannot open files on the Z: drive). It holds the `.est`, its `.CalBack`, a review spreadsheet and a READ ME.
5. In Callidus, rooms named "CHECK - " need a look. No room has a layout yet: select a room and press F9.

Products confirmed on one drawing are kept on the project and used as the starting choice for the next drawing, including finishes deliberately left out.

## What the software will not do

- Guess. A finish with no clear product match is left out until the estimator chooses one.
- Measure finishes drawn only in black linework (often matwells). They are listed under "Measure by hand".
- Cut openings out of a Callidus room yet. Rooms with an opening or another finish inside are marked "CHECK - " and the reason says how much to cut out.
- Read more than one page per drawing in one go. A floor on two sheets (Shellybanks) is two drawings, so two Callidus files.

## Running it

The web app queues the work; the worker (`web/worker.py`) reads drawings and writes files. A large A0 drawing takes up to about three minutes to read; the queue allows 30 minutes per job. For local use without Redis, set `JOBS_INLINE=1` and the work runs inside the request.
