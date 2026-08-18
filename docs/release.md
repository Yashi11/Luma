# macOS release process

## Local unsigned verification

```bash
cd desktop
npm ci
CSC_IDENTITY_AUTO_DISCOVERY=false npm run package:dir
plutil -lint "dist/mac-arm64/Visual Copilot.app/Contents/Info.plist"
```

This assembles the complete app but intentionally skips signing. CI runs the same gate on Apple Silicon.

## Signed distribution

Apple distribution requires a Developer ID Application certificate and notarization credentials. Configure electron-builder's standard `CSC_LINK`, `CSC_KEY_PASSWORD`, `APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, and `APPLE_TEAM_ID` secrets in the private repository, then run:

```bash
cd desktop
npm ci
npm run package:mac
```

Never commit certificates, passwords, API keys, or provider credentials. The application reads model-provider configuration from the launch environment for this prototype; a Keychain-backed settings surface should precede a broader beta.
