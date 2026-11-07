# Inventory service

Small HTTP service (version and health endpoints) released through the
pipeline in this repository. Each release cycle keeps its evidence under
`evidence/<scenario-id>/` and its release tag under `release/<scenario-id>`.

Local commands:

```bash
python -m unittest discover -s tests -v     # unit tests
python tools/build.py                       # deterministic dist/app.pyz
python tools/scan_gate.py                   # credential scan gate
docker compose -f infra/compose.yml up -d   # dev / qa / staging on 18091-18093
python tools/deploy_health.py --out evidence/<scenario-id>
```
