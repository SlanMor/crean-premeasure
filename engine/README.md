# premeasure-engine

The measuring engine: reads Callidus files today; drawings and Callidus writing come next.

```bash
pip install -e ".[dev]"
premeasure est path/to/floor.est            # rooms, materials, net areas, edge products
premeasure calback path/to/floor.est.CalBack # sheet placement
premeasure reconcile --xlsx measure.xlsx *.est
pytest -q
```

## Test fixtures

Real jobs are client data and stay out of git. To run the fixture tests, put the files in a folder outside the repository:

```
fixtures/
  limerick/      .est, .CalBack, measure .xlsx
  cashel/
  shellybanks/
```

and run `PREMEASURE_FIXTURES=/path/to/fixtures pytest -q`. Without it, those tests are skipped.
