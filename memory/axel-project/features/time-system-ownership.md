---
project_id: AXEL
plane_project_id: 5b59d8b8-f4fc-4430-a048-01fb453a80ab
workspace_id: axel_project
document_type: architecture_decision
topic: deadlines scheduled work and actual time ownership
status: active
last_verified: 2026-07-14
source_type: approved_implementation_brief
---

# Time-system ownership

The planned ownership boundary is:

- Plane owns deadlines and formal project state.
- Google Calendar owns scheduled work blocks.
- Kimai owns actual time recorded.

This separation avoids turning primary Markdown into a schedule, task database, or time ledger. It also avoids treating intended work, scheduled work, and actual time as the same fact.

For the current project-state model, Google Calendar and Kimai integration are planned rather than implemented. Kimai implementation is explicitly outside the present task.
