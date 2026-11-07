"""Live operations harness (additive module; no existing file is modified).

From a checkout of this repository, authenticate the GitHub CLI once, then:

    python -m liveops.operator

which creates a temporary private repository under your own account, performs
a complete release sequence there through real git and GitHub operations, and
audits the resulting evidence with the unmodified closure verifier against the
recorded expectations in protocols/oracle_locked.csv. See the liveops section
of the repository README.
"""
