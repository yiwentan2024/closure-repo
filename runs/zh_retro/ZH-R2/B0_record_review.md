# B0 record review — ZH-R2

Record review for release `R-FB-2026-07-8.9.3.6` as of cutoff
`2026-07-29T00:45:05Z`. No gate was re-executed: the owner ledger's
approval/deployment rows are read as recorded.

Gate construction (declared in `scenarios/owner_records/DERIVATION.md`):
each `approval` or `deployment` row of the owner ledger yields one required
gate; `disposition=admitted` maps to PASS and every other value to FAIL.
These are constructed record gates, not a tool-native gate log.

- `G-SIG-FB-260729-015`: **FAIL** at `2026-07-29T00:40:00Z`
