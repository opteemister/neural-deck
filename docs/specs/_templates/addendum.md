---
addendum: {slug}
spec: {parent-spec-slug}
status: proposed         # proposed -> in-discussion -> approved -> integrated | rejected
reason: |
  What the original spec missed and why this change is needed.
  The context of causes is mandatory — it feeds the knowledge base and the spec-quality metric.
author: {name}
approved_by: []
created: {YYYY-MM-DD}
updated: {YYYY-MM-DD}
affected_tasks: []       # which tasks to rewrite — set by the orchestrator
exemptions: []           # exemptions this addendum declares (same shape as in the spec frontmatter);
                         # such an addendum must pass a targeted spec-reviewer check before integration
---

<!--
File name: same date-prefix as the parent spec + a short change-title suffix,
e.g. 2026-07-10-billing.addendum-refund-flow.md next to 2026-07-10-billing.md.
Delta headers below make the change machine-readable; on section conflicts the newer document wins.
Only addenda with status: integrated count as part of the effective spec.
-->

## ADDED Requirements

## MODIFIED Requirements

## REMOVED Requirements
