# User-Annotated Visual Copilot for macOS

V1 foundation for a privacy-first, explicit visual explanation flow:

`hotkey → rectangle selection → exact in-memory crop → preview → explain`

The capture, lifecycle, OpenAI provider, and outbound-data contracts live in `src/visual_copilot`. The package remains independent of Electron so these invariants can be tested before the adapter is wired into the CoCo shell.

## Development

```bash
uv sync --extra capture
uv run python -m unittest discover -s tests -v
```

Set `OPENAI_API_KEY` before making a provider request. The provider uses the official OpenAI Responses API at `https://api.openai.com/v1`, defaults to `gpt-5.6`, sends the PNG as a base64 image input, requests structured output, disables response storage, and supplies no tools.

Actual screen capture uses the optional `capture` extra (`mss` and Pillow). Pure contract tests remain runnable without macOS screen permission or capture dependencies.

## V1 privacy contract

Only a decoded, dimension-checked PNG cryptographically bound to the frozen selection context can reach the provider adapter. The user question defaults to `Explain this.` and outbound metadata is allowlisted. No AX data, app/window identity, cursor history, arbitrary image bytes, or screenshot path is accepted by the outbound gate.

## Electron integration contract

`LocalSelectionService` is the trusted main-process boundary. Give its per-launch capability token only to the CoCo main-process adapter, never to arbitrary page content. The required order is:

1. `activate` with the active display snapshot;
2. `freeze` after mouse-up;
3. hide the complete overlay and wait for the hidden-state acknowledgement;
4. call `overlay_hidden`, then `capture`;
5. show the returned PNG locally and call `preview` with the question;
6. call `send` only after Enter, or `cancel` on Escape.

The V1 CoCo startup path must not start the continuous observer or register its old full-monitor capture handler. This repository does not contain the CoCo Electron source, so that final shell wiring must be applied in the CoCo checkout.
