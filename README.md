# Crean pre-measure

Reads architects' finishes drawings and produces Callidus files with every room already measured, named and assigned its finish, plus a quantities spreadsheet in Crean's format. The estimator checks the work and prices it in Callidus.

Live at: https://crean.markgaffney.com

## Layout

| Folder | What it is |
| --- | --- |
| `engine/` | Measuring engine (Python): reads drawings and Callidus files, writes Callidus files and Crean's spreadsheets. |
| `web/` | Django platform: tenders, products, drawings, room checks, queries, quantities, settings. |
| `docs/` | Callidus format notes and deploy steps. |
| `Dockerfile`, `docker-compose.yml`, `Caddyfile` | Server setup: app, worker, Postgres, Redis, HTTPS. |

## Status

- Reads finishes drawings (the architect's PDF export, or a scan) and writes pre-measured Callidus jobs that open in Callidus: rooms measured, named and given their product, skirting on room edges, background placed.
- Platform: tender set-up wizard, products per tender, measuring rules, bulk upload, room checking, queries for the architect, tender quantities, and the round trip: checked Callidus jobs uploaded back give Crean's measure spreadsheet and the accuracy of the pre-measure.
- How to use it: `docs/platform.md`. How drawings are read and how accurately: `docs/drawing-extraction.md`. The Callidus file format: `docs/callidus-format.md`. The server: `docs/deploy.md`.

## Running locally

```bash
pip install -e "engine[dev]" && pip install -r web/requirements.txt
pytest -q engine/tests
cd web && DJANGO_DEBUG=1 python manage.py migrate && python manage.py runserver
```

## Client data

Drawings, Callidus files (`.est`, `.CalBack`) and measure spreadsheets are client data. They are never committed to this repository. Test fixtures live outside git (see `engine/README.md`).
