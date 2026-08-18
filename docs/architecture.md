# V1 architecture boundary

The Electron shell owns the global shortcut, the interactive full-display overlay, keyboard cancellation, and preview card. It sends only display geometry and the question over a per-launch authenticated IPC/localhost boundary.

The Python selection service owns:

1. the active display snapshot and DIP → capture-pixel mapping;
2. region-only in-memory capture after the overlay has been hidden;
3. crop validation and hashing;
4. strict outbound request construction; and
5. the provider call and structured `{explanation, uncertainty, needs_more_context}` response.

The V1 invariant is that the observer path is not a capture trigger. No AX tree, app name, window title, cursor history, or screenshot file is part of the strict request.
