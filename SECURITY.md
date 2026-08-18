# Security policy

Please report vulnerabilities privately through GitHub Security Advisories for this repository. Do not include real screenshots, credentials, API keys, customer data, or private messages in a report.

## Security boundary

V1 sends only the selected crop, the explicit question (or `Explain this.`), and the minimum provider request fields. It does not send Accessibility data, application names, window titles, input history, or a full-screen frame. Crops remain in process memory, expire after five minutes, and are consumed on the first send attempt.

Text inside a selected image is untrusted content. The model has no tools or action APIs.
