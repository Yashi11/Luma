# V1 architecture boundary

The Electron shell owns the global shortcut, the interactive full-display overlay, keyboard cancellation, and preview card. It sends only display geometry and the question over a per-launch authenticated IPC/localhost boundary.

The Python selection service owns:

1. the active display snapshot and DIP → capture-pixel mapping;
2. region-only in-memory capture after the overlay has been hidden;
3. crop validation and hashing;
4. strict outbound request construction; and
5. the provider call and structured `{explanation, uncertainty, needs_more_context}` response.

The V1 invariant is that the observer path is not a capture trigger. No AX tree, app name, window title, cursor history, or screenshot file is part of the strict request.

## Implemented runtime contracts

- `SelectionCaptureContext` freezes a tagged rectangle, complete display bounds, configuration identity, mapped crop, timestamp, and capture ID.
- Rotated displays and changed display snapshots fail closed in V1.
- The capture adapter receives the frozen context rather than an arbitrary region. It captures only the absolute mapped crop and validates PNG structure, checksums, decoded dimensions, black/protected frames, 16 MP, and 10 MB limits.
- `CapturedCrop` provenance binds the PNG hash, crop, and capture ID. The strict outbound gate rejects altered or caller-invented image bytes.
- `SelectionSession` enforces geometry-frozen → overlay-hidden → captured → preview → send and permits cancellation only before send. Provider failure retains the same validated in-memory crop for explicit retry.
- `LocalSelectionService` requires a constant-time-checked per-launch capability token and rejects unknown renderer fields.
- `OpenAIVisionProvider` uses the OpenAI Responses API with a base64 PNG, structured explanation schema, `store=False`, and an empty tools list.

## Remaining shell integration

The CoCo Electron checkout is not present in this repository. Its adapter still needs to create `SelectionOverlay`, bind `globalShortcut`, pass the display snapshot and rectangle into `LocalSelectionService`, render preview/answer cards, and bypass the continuous observer and old full-monitor capture route.
