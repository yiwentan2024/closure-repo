# B0 record review — ZH-R4

Record review for release `R-FB-2026-08-NORMAL-07` as of cutoff
`2026-08-18T06:12:05Z`. No gate was re-executed: the owner ledger's
approval/deployment rows are read as recorded.

Gate construction (declared in `scenarios/owner_records/DERIVATION.md`):
each `approval` or `deployment` row of the owner ledger yields one required
gate; `disposition=admitted` maps to PASS and every other value to FAIL.
These are constructed record gates, not a tool-native gate log.

- `G-SIG-FB-260820-010`: **PASS** at `2026-08-18T06:12:00Z`

## Identity-pending signal

SIG-FB-260820-010 is identity-pending. The selected V23 ledger records a
deployment event on 2026-08-18 14:12 (local, admitted). The provider's R4
evidence package records a change event for the same operation id on
2026-08-20 06:25Z (14:25 local, admitted), and the provider's v23 manifest
marks the id `EXCLUDED_AS_DUPLICATE_IDENTITY` with
`EVIDENCE_IDENTITY_PENDING`. The ledger follows the producer's standing
exclusion; the conflict is the subject of this audit, not an input to the
closure verdict.

## Not covered by the shipped records

The original R4 scenario text claimed "6/6 dispositions" and a snapshot
that "stays frozen". Neither fact is covered by the shipped records: the
selected 31-row ledger and the R4 evidence package contain no six-of-six
disposition set and no snapshot or frozen-state record. The audit does not
assert either claim; both are marked NOT_COVERED.
