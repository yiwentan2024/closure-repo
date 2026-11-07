# Frozen reference package

`EvidenceClosure/` is an earlier verifier package retained byte-identical for
regression comparison. Every file is reproduced exactly as received, including
its internal manifests, audit CSVs and original formatting.
`tools/check_frozen_manifest.py` verifies the package against its own
`S9_package_manifest.csv`.

`source_archive_metadata/` is the package metadata that was received alongside
the frozen package. It predates this repository and is kept unchanged so the
frozen comparison stays byte-exact. It is not an input to the closure auditor
and is not used by any check in this repository; member lists in that metadata
describe the original package, not the files shipped here.
