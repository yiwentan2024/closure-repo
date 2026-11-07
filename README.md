# Release Closure Auditing

A small inventory service with a three-configuration CI/CD pipeline and a
dual-timepoint closure auditor. The auditor decides whether every release
obligation is closed by evidence that existed before the release cutoff, and
records the failure codes when it is not.

The repository contains four verification tracks, in the order a reviewer can
run them:

1. **Live operations on GitHub** — `liveops/` performs a complete release
   sequence through real git, GitHub and Docker operations in a temporary
   repository and audits the evidence those operations produce.
2. **Controlled scenarios** — 16 failure and control scenarios executed through
   the real pipeline in three configurations.
3. **Progressive gating** — staged promotion through dev / qa / staging, with
   and without the closure audit.
4. **Retrospective audits** — three release-record audits over records supplied
   by the source project (ZH-R2/R3/R4).

## 1. Live operations: independent verification

`liveops/` is the shortest end-to-end path for an independent reviewer. One
command creates a temporary private repository under your own GitHub account
and runs 16 release cycles inside it. Each cycle performs the ordinary
operations of a release: a code change and pull request, review and merge,
locally executed unit and secret-scan gates, a three-container deployment with
real HTTP health probes, alert issues and on-call triage, an annotated tag, a
GitHub Release, and the built artifact. Every fact the auditor reads is produced
by one of those operations and remains reviewable on GitHub.

The harness exists because closure auditing depends on release histories that
exhibit a specific failure mode at a specific time. Such histories are rare in
normal operation, and writing the evidence files by hand would test nothing.
The harness therefore constructs the conditions and lets the real operations
produce the evidence. It never edits derived audit files: a scenario changes
what the people and the pipeline do, not what the auditor records.

### What one run does

- creates ONE private repository named `lab-live-batch-<UTC>` under the
  authenticated account (one repository per run, not per scenario);
- runs each scenario in its own namespace: own feature branch, `evidence/<SID>/`,
  own `release/<SID>` tag whose tagger date is that scenario's cutoff;
- audits each cycle with the unmodified `verifier/closure_audit.py`;
- cross-checks each verdict with an in-repo `closure-audit.yml` workflow
  dispatch that runs the same vendored verifier byte-for-byte;
- writes the facts, the verifier output, the narrated action log, and the
  anchors (tag, commit, release, audit run) under `runs/live/<UTC>/<SID>/`;
- keeps the batch repository so every operation remains reviewable.

Policy and the evidence collection rule are read from the frozen release tag,
never from the current worktree; required material that is missing fails
collection closed instead of being defaulted.

### Setup

1. Install `uv`, `gh`, `git`, and `docker`.
2. Authenticate the GitHub CLI:
   ```bash
   gh auth login                # HTTPS, browser login
   gh auth status               # token scopes: repo, workflow, delete_repo
   gh auth refresh -h github.com -s workflow,delete_repo   # if either is missing
   ```
   `workflow` is needed to push the seed's workflow files; `delete_repo` is
   needed only by the cleanup tool. The operator checks `repo` and `workflow`
   before it starts.
3. Create the environment (Python >= 3.12):
   ```bash
   uv sync
   source .venv/bin/activate
   ```

### Commands

```bash
python -m liveops.operator --plan        # print the 16 scenarios and their recorded outcomes
python -m liveops.operator               # run all 16 scenarios in one batch repository (60-90 min)
python -m liveops.operator --scenario N01,A101
python -m liveops.operator --root runs/live/<dir>            # resume; skip scenarios already MATCHed
python -m liveops.operator --root runs/live/<dir> --reaudit  # re-dispatch in-repo audits only
```

An offline replay of the recorded scenarios runs without GitHub or Docker:

```bash
python -m unittest discover -s liveops/tests -t . -v
```

### Reading the output and trusting the verdict

For each scenario the operator prints the recorded expectation before the
cycle, narrates every real action during the cycle, and prints a verdict block
afterwards. The observed verdict is not a claim — it is a file you can
recompute:

- `runs/live/<dir>/<SID>/case.json` — the exact facts the verifier consumed
  (assembled from the repository's own git history, evidence files and GitHub
  records);
- `runs/live/<dir>/<SID>/result.json` — the verifier's output, unmodified;
- recompute it yourself:
  ```bash
  python -m verifier.closure_audit --input runs/live/<dir>/<SID>/case.json \
      --output /tmp/recheck.json --allow-open
  ```
- the in-repo audit workflow run (URL printed per scenario and stored in
  `story.json`) must produce the same decision and codes — two independent
  executions of the same verifier on the same repository. Its run conclusion
  mirrors the verdict by design: **green = CLOSED, red = OPEN**, and the
  decision plus failure codes are written to the run summary page.

The final `matrix.txt` lists all scenarios (recorded vs observed vs in-repo),
each scenario's anchor URLs (release tag, frozen commit, audit run), and the
cleanup command.

### Cleanup

Batch repositories are kept so every operation stays reviewable on GitHub.
When you are done, delete the batch you reviewed (or every batch explicitly):

```bash
python -m liveops.cleanup --root runs/live/<dir>        # show that run's batch repo
python -m liveops.cleanup --root runs/live/<dir> --yes  # delete it
python -m liveops.cleanup --repo owner/lab-live-batch-X --yes   # delete by name
python -m liveops.cleanup --all                         # list every lab-live-* repo
python -m liveops.cleanup --all --yes                   # delete every one
```

Without `--yes` nothing is deleted; without `--root`, `--repo` or `--all` the
tool only lists. Deletion is restricted to `lab-live-*` repositories owned by
the authenticated account, and any deletion failure is reported as a non-zero
exit.

Outputs land in `runs/live/<UTC timestamp>/` (git-ignored).

## 2. Controlled scenarios

The same 16 scenarios are defined as machine-readable cases in
`scenarios/manifest.json` and executed by `.github/workflows/e2-19cases.yml`
under three configurations of the same pipeline:

- **B0** — unit tests, deterministic build, one Docker image deployed to three
  isolated stages (dev / qa / staging) with HTTP health gates and digest
  pinning.
- **B1** — B0 plus GitHub Artifact Attestations (issue and verify; a replaced
  artifact must fail).
- **B2** — B1 plus the dual-timepoint closure checker.

The batch is 16 scenarios × 3 configurations × 3 replicates = 144 runs,
preceded by 7 calibration runs. Every run executes the real tests, build, stage
deployments and (in B1/B2) real attestation at the same commit. Each run's B2
decision and failure codes are compared with `protocols/oracle_locked.csv`,
which is committed before the first run and never edited afterwards. The batch
definition is frozen in `protocols/batch_protocol.md`; the run plan is
`protocols/E2_Controlled_Run_Plan.csv`.

The 3 retrospective cases (ZH-R2/R3/R4) are outside this batch and are covered
in section 4.

Local execution of all 16 cases:

```bash
python -m tools.e2_local_suite
```

## 3. Progressive gating

`progressive-gating/` promotes one deterministic artifact through three real
Docker stages (dev → qa → staging). Each stage must pass an image-identity
check, a pinned artifact-digest check, an attestation-binding check and a live
HTTP health check before the next stage is requested. Two arms use the same
build, image and promotion chain:

- **P0 (B1)** — staged promotion with the minimal collector; the closure audit
  is not configured.
- **P1 (B2)** — the same staged promotion with the closure audit after the
  staging gate passes.

The delivered batch uses one scenario × one repeat per workflow run: 6 scenarios
× 3 repeats = 18 formal runs, plus 3 calibration runs — 21 workflow runs and 42
attempts. Two baseline controls (PG-ART, PG-QA) stop at the failing stage and
are reported separately.

Full commands and the offline verification procedure are in
`progressive-gating/README_Reproduction.txt`.

## 4. Retrospective audits over supplied records (ZH-R2/R3/R4)

Three retrospective closure audits over release-disposition records supplied by
the source project. These are not constructed cases: the facts are the records.

- `scenarios/owner_records/owner_release_records.xlsx` — the selected records
  (31 rows covering the three audited releases), committed as the source of
  facts. It is generated by `scenarios/owner_records_select.py` from the
  provider's export workbook (identified by SHA-256 `5ba66360…379459`; the
  selected file's own SHA-256 is `1944b225…56fac`); role names are translated to
  English per a declared vocabulary, all other values are copied verbatim.
  Selection rules, gate construction and the full hash chain are in
  `scenarios/owner_records/DERIVATION.md`.
- `scenarios/owner_records/R4_EVIDENCE/` — a derived copy of the provider's R4
  event files with two export-metadata columns removed and all event fields
  unchanged. It is case-external context for the recorded identity conflict
  (`SIG-FB-260820-010`), not an input to the B2 verdict.
- Each ZH case rebuilds its facts from the xlsx at a declared business moment
  (see `zh_anchor` in each case file), runs the same B2 auditor as the controlled
  scenarios, and archives the adjudication, the B0 record review, and the B1
  not-applicable record under `runs/zh_retro/`.
- The three archived adjudications (all `OPEN/NFI`: the auditor refuses closure
  on records that do not carry the evidence forms it requires) are reproducible
  in any clone: `python -m unittest tests.test_owner_records`.

R3's record anchor is 09:55:05 with the 10:56 re-adjudication left post-cutoff;
the supplied summary face 11:00/11:10 is a declared anonymization-drift label. R4's
original "6/6 dispositions" and frozen-snapshot claims are marked `NOT_COVERED`:
the shipped records contain neither.

## Verification

Verification uses this repository alone:

1. **Outcome claims** — compare any run's B2 decision against the recorded
   expectations in `protocols/oracle_locked.csv` (committed with the initial
   commit).
2. **Reproduction** — run locally or push this tree to a new public GitHub
   repository and dispatch the workflows with the same inputs; outcomes are
   identical.
3. **No pre-stored answers** — `verifier/closure_audit.py` computes every
   decision from the case facts (dispositions, digests, timings, findings); it
   never reads the oracle. Inspect the source and the test suites (`tests/`).

The batch protocol is frozen in `protocols/batch_protocol.md`.

## Repository layout

- `app/`, `tests/`, `tools/`, `infra/` — the inventory service, unit tests,
  build / stage-check / dispatch utilities, Dockerfile and compose file
- `verifier/` — the dual-timepoint closure auditor
- `scenarios/` — machine-readable case definitions, the case builder, and the
  supplied owner records
- `protocols/` — the 144-run controlled plan, the batch protocol, and the
  recorded expected outcomes
- `progressive-gating/` — staged-promotion evaluation
- `liveops/` — live-operations harness and its seed product repository
- `frozen_reference/` — an earlier verifier package kept byte-identical for
  regression comparison; `tools/check_frozen_manifest.py` verifies its S9
  manifest. Its CSVs are reproduced exactly as received, including the ragged
  internal formatting of S7 and S21
- `.github/workflows/` — `E2 19-Case Controlled Evaluation`, `progressive-gating`,
  and `Original Frozen Reference Reproduction` (a separate regression)

No credentials are stored in the repository; all service data is local to the
checks.

## Run locally (Linux or macOS, Python 3.12+)

Windows is not supported: the checks compare POSIX paths and rely on
`NamedTemporaryFile` and `git archive` semantics that differ on Windows.
The recorded runs used GitHub Actions `ubuntu-24.04` with CPython 3.12.

```bash
python tools/check_frozen_manifest.py                       # frozen package integrity (S9)
python -m tools.run_frozen_regression --mode smoke --out runs/original_smoke
python -m unittest discover -s tests -v
python -m tools.e2_local_suite                              # all 16 controlled cases
python -m unittest tests.test_owner_records                # retrospective verdicts
python -m scenarios.build_case --scenario A202 --out runs/try_A202
python -m verifier.closure_audit --input runs/try_A202/case.json --output runs/try_A202/result.json --allow-open
python -m tools.e2_dispatch --scenario ALL --variant ALL    # dry-run
```

For a reproduction record, log the OS, Python version, each command, exit
code, duration, and the SHA-256 of stdout and stderr.

## If this tree came from the evidence archive

The archive root contains `MANIFEST.sha256` next to `source/`. Verify the file
set and bytes before running anything:

```bash
sha256sum -c MANIFEST.sha256        # macOS: shasum -a 256 -c MANIFEST.sha256
cd source
```

`source/` is a complete, runnable copy of the repository at the recorded commit.
The live-operations harness can be run from `source/` directly; it creates its
own batch repository, so no repository URL needs to be configured.
