# 0.5.0 release preparation

This candidate repairs the split-distribution work omitted from 0.4.0.
It also includes the completed feature fixes and UI/UX updates for 0.5.0.
The distribution changes are reviewed in PR #13; this dependent feature PR is reviewed separately.
Both PRs are intended for one 0.5.0 release. A standalone 0.4.1 release is not planned.
Additional manual images remain in progress and will be added before final publication.
It is a draft, not a published split release. Version 0.4.0 remains available as an all-in-one ZIP.

| Requirement | Candidate status |
|---|---|
| Restore distribution CPU checks and generation of full/main/runtime artifacts | Restored in this candidate; CI results are attached to the PR |
| Reject a workflow missing the split-distribution requirements | Independent release-scope CI and omission regression tests added |
| Public repository index, Release assets and installation URL | Publication/catalog/site code connected and rehearsed offline; actual activation and public HTTPS checks remain pending |
| Main-component third-party notices | Corrected the conflicting bundled-wheel introduction; component credits preserved |
| Separate runtime distribution terms | Pending review |
| Installation and update through the public URL in Blender | Pending; local package validation is insufficient |

The publication gate is deliberately closed in `.github/release_scope.json`.
The current tag workflow must not be used to publish this candidate while these requirements remain open.
Restoring the preview workflow alone does not complete production publishing.

The branch README can be viewed before merging. The documentation workflow builds PRs,
but currently deploys the public site only from main; there is no hosted documentation PR preview.

A loopback candidate repository/page is being tested separately from public publishing.
The current Windows UI automation tool rejected the cross-window browser-to-Blender drag
before delivering it. This is not a passed drag-and-drop test. Testing the same URL with
Blender's standard installation operator is a separate, limited check.

The maintainer subsequently confirmed successful drag-and-drop main installation
from the local candidate on 2026-11-25. This is separate from AI URL/operator checks
and does not prove installation/update from the future public HTTPS repository.

See [publishing instructions](tools/PUBLISHING.md) for immutable asset promotion,
the persistent catalog branch, whole-site deployment and required approval.
Both publication completion and asset authorization remain explicitly false.
