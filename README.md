# Visual Copilot for macOS

Point at anything on your Mac and ask about it without changing context.

Visual Copilot reuses the CoCo Electron shell while replacing its default continuous-observation path with an explicit, user-initiated flow:

`hotkey → rectangle selection → exact in-memory crop → preview/question → explain`

## V1 privacy contract

Only a decoded, dimension-checked PNG cryptographically bound to the frozen selection context can reach the OpenAI provider adapter. The question defaults to `Explain this.` and outbound metadata is allowlisted. No AX data, app/window identity, cursor history, arbitrary image bytes, screenshot path, observer history, or full-monitor fallback is accepted.

The V1 model receives no tools and returns structured `{explanation, uncertainty, needs_more_context}` data. If more context is needed, the answer card offers a new selection rather than silently broadening capture.

## Architecture

- **CoCo Electron shell:** global shortcut, interactive `SelectionOverlay`, preview/question card, answer card, and trusted main-process IPC.
- **Python selection service:** immutable display snapshot, DIP-to-pixel mapping, region-only `mss` capture, PNG verification, hash/provenance, cancellation, and retry lifecycle.
- **OpenAI Responses API:** base64 PNG input, structured output, `store=False`, and no tools.

The observer, observer input listeners, old full-monitor hotkey route, TTS, Accessibility enrichment, dataset collection, and autonomous actions are disabled for V1. CoCo's intentional avatar, settings, conversation, and local-history surfaces remain available.

## Development

```bash
uv sync
uv run pytest

cd desktop
npm install
npm start
```

Set `OPENAI_API_KEY` before sending a selection. The OpenAI endpoint is fixed to `https://api.openai.com/v1`; the default model is `gpt-5.6-sol` and can be changed in the trusted service configuration.

See [docs/architecture.md](docs/architecture.md) for the implemented contracts and remaining test matrix.

## Upstream

The desktop shell is based on [collaborative-agents/coco](https://github.com/collaborative-agents/coco) under the Apache License 2.0. See [LICENSE](LICENSE) and [PRIVACY.md](PRIVACY.md).
