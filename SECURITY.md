# Security Policy

## Supported release

The operational Python pipeline introduced in version 2.0.0 is the supported runtime for security fixes.

## Reporting

Report security-sensitive findings through GitHub's private vulnerability reporting feature when it is available for this repository. Do not publish secrets, private media, tokens, or exploitable details in a public issue.

## Operator responsibilities

- Process only media you are authorized to use.
- Keep the application behind authentication before exposing it beyond a trusted network.
- Store run workspaces on access-controlled storage.
- Do not place secrets in briefs, manifests, example files, or source control.
- Keep `SILVER_SCREEN_DEBUG=0` in production.
- Apply platform-level malware scanning and content controls for public upload deployments.

Silver-Screen does not execute uploaded code. Voice files are inventoried but are not cloned or synthesized in this release.
