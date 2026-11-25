# 0.4.1 release preparation

This candidate repairs the split-distribution work omitted from 0.4.0.
It is a draft, not a published split release. Version 0.4.0 remains available as an all-in-one ZIP.

| Requirement | Candidate status |
|---|---|
| Restore distribution CPU checks and generation of full/main/runtime artifacts | Restored in this candidate; CI results are attached to the PR |
| Reject a workflow missing the split-distribution requirements | Independent release-scope CI and omission regression tests added |
| Public repository index, Release assets and installation URL | Pending production connection |
| Main-component third-party notices and separate runtime distribution terms | Pending correction and review |
| Installation and update through the public URL in Blender | Pending; local package validation is insufficient |

The publication gate is deliberately closed in `.github/release_scope.json`.
The current tag workflow must not be used to publish this candidate while these requirements remain open.
Restoring the preview workflow alone does not complete production publishing.

The branch README can be viewed before merging. The documentation workflow builds PRs,
but currently deploys the public site only from main; there is no hosted documentation PR preview.
