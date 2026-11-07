"""Single-source record-review texts for the ZH retrospective audits.

Both the workflow step (`tools/zh_audit.py`) and the local archive runner
(`tools/zh_retro_run.py`) import these functions, so the B0/B1 wording and the
R4 identity disclosure cannot drift between the two paths.
"""
from __future__ import annotations

ZH_VARIANT = "B2"
ZH_SCENARIOS = ("ZH-R2", "ZH-R3", "ZH-R4")

R4_IDENTITY_NOTE = (
    "SIG-FB-260820-010 is identity-pending. The selected V23 ledger records a\n"
    "deployment event on 2026-08-18 14:12 (local, admitted). The provider's R4\n"
    "evidence package records a change event for the same operation id on\n"
    "2026-08-20 06:25Z (14:25 local, admitted), and the provider's v23 manifest\n"
    "marks the id `EXCLUDED_AS_DUPLICATE_IDENTITY` with\n"
    "`EVIDENCE_IDENTITY_PENDING`. The ledger follows the producer's standing\n"
    "exclusion; the conflict is the subject of this audit, not an input to the\n"
    "closure verdict.")

R4_NOT_COVERED = (
    "The original R4 scenario text claimed \"6/6 dispositions\" and a snapshot\n"
    "that \"stays frozen\". Neither fact is covered by the shipped records: the\n"
    "selected 31-row ledger and the R4 evidence package contain no six-of-six\n"
    "disposition set and no snapshot or frozen-state record. The audit does not\n"
    "assert either claim; both are marked NOT_COVERED.")

GATE_CONSTRUCTION = (
    "Gate construction (declared in `scenarios/owner_records/DERIVATION.md`):\n"
    "each `approval` or `deployment` row of the owner ledger yields one required\n"
    "gate; `disposition=admitted` maps to PASS and every other value to FAIL.\n"
    "These are constructed record gates, not a tool-native gate log.")

B1_NA = """# B1 record — N/A

The original system deployed no artifact-attestation tooling; per the batch
specification, B1 reports N/A for ZH retrospective cases rather than claiming a
miss. The owner ledger carries no attestation artifacts.
"""


def require_b2(variant: str) -> None:
    """A ZH audit is one B2 run carrying the B0 review and the B1 N/A record."""
    if variant != ZH_VARIANT:
        raise SystemExit(
            f"ZH retrospective audits run once as {ZH_VARIANT} (one run covers "
            f"B0 record review + B1 N/A + B2 adjudication); got variant={variant!r}")


def b0_review(sid: str, case: dict) -> str:
    gates = case.get("gates") or []
    lines = [
        f"# B0 record review — {sid}", "",
        f"Record review for release `{case['release_id']}` as of cutoff",
        f"`{case['source_cutoff']}`. No gate was re-executed: the owner ledger's",
        "approval/deployment rows are read as recorded.", "",
        GATE_CONSTRUCTION, "",
    ]
    if not gates:
        lines.append("No approval/deployment rows exist for this release, so the "
                     "case carries no gate evidence.")
    for gate in gates:
        lines.append(f"- `{gate['gate_id']}`: **{gate['status']}** at "
                     f"`{gate['evaluated_at']}`")
    if sid == "ZH-R4":
        lines += ["", "## Identity-pending signal", "", R4_IDENTITY_NOTE,
                  "", "## Not covered by the shipped records", "", R4_NOT_COVERED]
    return "\n".join(lines) + "\n"
