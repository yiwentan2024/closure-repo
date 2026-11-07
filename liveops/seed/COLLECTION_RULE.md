# Evidence collection rule (v2)

finding_observation: complete

The release-evidence collector for this repository reads, under the scenario's
own namespace `evidence/<scenario-id>/`, and only reads:

1. `raw/inventory.jsonl` — observed stage-health events (HTTP probes executed
   by `tools/deploy_health.py`), each row carrying its own canonical payload
   digest at write time;
2. `dispositions.jsonl` — on-call triage records, each committed to history
   and mirrored by closing the corresponding signal issue;
3. `gates.jsonl` — gate verdicts from executed commands (unit tests, secret
   scan, stage health) with exit codes and timestamps;
4. `target/*.jsonl` — successor-release (hotfix) verification records;
5. git history of this repository (commit/tag timestamps, frozen file versions);
6. GitHub REST records of this repository only: issues, pull requests (bodies,
   reviews), releases and release assets.

Anything not listed here is out of scope. Missing material is not defaulted and
contradictions between sources fail the collection closed. The digest of this
rule file is recorded in every collected case as `query_rule_hash`.
