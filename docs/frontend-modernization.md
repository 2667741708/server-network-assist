# Desktop UI modernization notes

## Current first-stage boundary

Server Network Assist continues to use Framework7 with static HTML, CSS, and JavaScript. The current UI refresh changes information architecture, status presentation, responsive layout, and confirmation affordances only. Network operations, typed action names, backend payloads, credential handling, host-key validation, proxy mutation, recovery semantics, and audit records remain outside the frontend change.

The UI should continue to treat backend status as the source of truth. It must not infer a dangerous operation from free-form diagnostic text, invent a metric that the backend does not provide, or display credential material after submission.

## Suggested future migration sequence

1. Extract typed view models from the existing status, fleet, proxy, diagnostics, and update payloads.
2. Add contract fixtures and browser tests for each existing endpoint before changing the rendering runtime.
3. Move one low-risk read-only surface first, preferably the overview status summary, behind a small component boundary.
4. Migrate confirmation and error handling as shared primitives before migrating mutation pages.
5. Keep the current typed backend API as the compatibility boundary while the view layer changes.

## Technology options

- React + TypeScript is the most practical incremental path for typed view models and component-level tests.
- Fluent UI is the closest visual fit for the Windows desktop operations console.
- shadcn/ui is viable if the project wants more local control over markup and styling, but it would require a deliberate accessibility and visual-token baseline.

Do not combine a framework migration with changes to SSH routing, WireGuard operations, proxy mutation, recovery, or credential security. Those should remain independently reviewable.
