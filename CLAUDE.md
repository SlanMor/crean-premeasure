# Working on crean-premeasure

- Product: reads finishes drawings, writes pre-measured Callidus files and Crean's quantities spreadsheet. The functional spec is the source of truth for scope.
- Accuracy rule: never output a guessed quantity. Anything uncertain is flagged for the estimator, not filled in.
- Client data (drawings, .est, .CalBack, spreadsheets) never goes into git. Fixture tests read from `PREMEASURE_FIXTURES`.
- Layout: `engine/` is the measuring engine (pure Python, no Django). `web/` is the Django platform; it calls the engine, never the reverse.
- Licences: use pdfium (pypdfium2) for PDFs. Do not add PyMuPDF (AGPL).
- Writing: British English. No em dashes in user-facing text.
- Before committing: `pytest -q engine/tests`, `python manage.py check` and `makemigrations --check` in `web/`.
- The Callidus format notes live in `docs/callidus-format.md`. Update them when anything new is decoded.
