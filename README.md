# User-Annotated Visual Copilot for macOS

V1 foundation for a privacy-first, explicit visual explanation flow:

`hotkey → rectangle selection → exact in-memory crop → preview → explain`

The capture and outbound-data contracts live in `src/visual_copilot`. The package is intentionally independent of Electron so the coordinate and privacy invariants can be tested on every platform before wiring macOS window capture and the UI shell.

## Development

```bash
python3 -m unittest discover -s tests -v
```

Optional capture support uses `mss` and PNG encoding uses Pillow. They are kept optional so geometry/privacy tests remain runnable in CI without screen permissions.

## V1 privacy contract

Only the validated selected crop, the user question (defaulting to `Explain this.`), and provider metadata explicitly supplied by the caller may leave the process. No full-screen frame, AX data, app/window identity, cursor history, or screenshot file is accepted by the outbound gate.
