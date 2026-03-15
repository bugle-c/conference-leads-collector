# Dashboard Action Feedback Design

## Goal

Make dashboard actions feel responsive immediately after click and clearly show `started`, `success`, and `error` states.

## Scope

- Dashboard page only.
- Actions: seed import, run once, run batch, TenChat discovery.
- No external UI libraries.

## UX

- Show toast notifications in the top-right corner.
- Show immediate pending feedback on click.
- Disable only the clicked button while its request is in flight.
- Restore the button label after completion.
- Keep the existing result box as detailed API output.

## Implementation

- Add a `toast-stack` container to the dashboard template.
- Add lightweight CSS for `info`, `success`, and `error` toasts.
- Add one shared JS helper that:
  - updates button state
  - shows pending toast
  - runs the fetch
  - writes the result box
  - shows success/error toast from HTTP status/payload

## Non-Goals

- No cross-page notification system.
- No persistence across reloads.
- No background polling or job progress tracking.
