---
spec: {slug}
title: {Title}
status: draft            # draft -> in-discussion -> approved -> in-implementation -> completed | superseded
version: 1               # increment while editing before approval; immutable after approved
author: {name}
participants: []         # people + agents taking part in the discussion
approved_by: []
approved_at:
created: {YYYY-MM-DD}
updated: {YYYY-MM-DD}
tags: []
exemptions: []           # sanctioned deviations from constitution III/IV or tech-defaults (constitution V.5), each:
                         #   - rule: {e.g. IV.8}
                         #     reason: {why the spec requirement outweighs the default}
                         #     intent_preserved_by: {how the rule's purpose is preserved by other means}
feature_branch:          # set by the orchestrator when implementation starts
result:                  # path to docs/spec-results/... — set by /finalize
---

# {Title}

<!--
Spec body. Rules (constitution + process):
- Requirements must be atomic and verifiable (code/test/run) — spec-reviewer will flag untestable wording.
- No TBD/placeholders — spec-reviewer blocks them.
- Spec altitude: describe WHAT and the contracts between components. Field-level minutiae (max lengths,
  message wording, display details) are developer discretion per tech-defaults ("detail resolution") —
  the spec-reviewer will not ask about them, and specifying them here only adds noise.
- Requirements MAY contradict the constitution (III/IV) or tech-defaults — but only via an explicit
  `exemptions` entry above (spec-reviewer proposes the wording when it finds an undeclared conflict).
  Constitution I/II are never overridable by a spec.
- After status: approved the file is immutable; changes go into addenda (same filename prefix, see _templates/addendum.md).
-->

## Goal

## Requirements

## Constraints

## Out of scope
