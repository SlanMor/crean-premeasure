# Crean pre-measure

Reads architects' finishes drawings and produces Callidus files with every room already measured, named and assigned its finish, plus a quantities spreadsheet in Crean's format. The estimator checks the work and prices it in Callidus.

Live at: https://crean.markgaffney.com

## Layout

| Folder | What it is |
| --- | --- |
| `engine/` | Measuring engine (Python). Reads Callidus .est and .CalBack files today; drawing extraction and Callidus writing are next. |
| `web/` | Django platform: project setup, products, uploads, background jobs, admin. |
| `docs/` | Callidus format notes and deploy steps. |
| `Dockerfile`, `docker-compose.yml`, `Caddyfile` | Server setup: app, worker, Postgres, Redis, HTTPS. |

## Status

- Phase 0, done: repository, server stack, Callidus reader that reproduces Crean's spreadsheets on three jobs.
- Phase 1, next: write a Callidus file that opens in Callidus. Needs four test files from Crean (see `docs/callidus-format.md`).

## Running locally

```bash
pip install -e "engine[dev]" && pip install -r web/requirements.txt
pytest -q engine/tests
cd web && DJANGO_DEBUG=1 python manage.py migrate && python manage.py runserver
```

## Client data

Drawings, Callidus files (`.est`, `.CalBack`) and measure spreadsheets are client data. They are never committed to this repository. Test fixtures live outside git (see `engine/README.md`).
