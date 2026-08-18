# User-Annotated Visual Copilot for macOS

V1 foundation for a privacy-first, explicit visual explanation flow:

`hotkey → rectangle selection → exact in-memory crop → preview → explain`

The capture and outbound-data contracts live in `src/visual_copilot`. The package is intentionally independent of Electron so the coordinate and privacy invariants can be tested on every platform before wiring macOS window capture and the UI shell.

## Run the desktop prototype

Requirements: macOS 13+, Node.js 20+, and Screen Recording permission for the terminal/app launching Visual Copilot.

```bash
cd desktop
npm install
VISUAL_COPILOT_API_KEY=... npm start
```

Press `⌘⇧E`, draw a rectangle on the display under the pointer, review the exact crop, optionally type a question, and press Enter. Configure an OpenAI-compatible provider with `VISUAL_COPILOT_API_URL`, `VISUAL_COPILOT_API_KEY`, and `VISUAL_COPILOT_MODEL`.

## Development and verification

```bash
python3 -m unittest discover -s tests -v
cd desktop && npm test
```

Optional capture support uses `mss` and PNG encoding uses Pillow. They are kept optional so geometry/privacy tests remain runnable in CI without screen permissions.

The desktop shell follows the transparent-window, global-shortcut, and hardened packaging patterns from [CoCo](https://github.com/collaborative-agents/coco), while deliberately bypassing CoCo's continuous observer in this V1.

## V1 privacy contract

Only the validated selected crop, the user question (defaulting to `Explain this.`), and provider metadata explicitly supplied by the caller may leave the process. No full-screen frame, AX data, app/window identity, cursor history, or screenshot file is accepted by the outbound gate.
