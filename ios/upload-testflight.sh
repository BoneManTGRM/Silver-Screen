#!/usr/bin/env bash
set -euo pipefail
umask 077

: "${APPLE_TEAM_ID:?Set APPLE_TEAM_ID to the Apple Developer team ID}"
: "${APPLE_KEY_ID:?Set APPLE_KEY_ID to the App Store Connect team key ID}"
: "${APPLE_ISSUER_ID:?Set APPLE_ISSUER_ID to the App Store Connect issuer ID}"
: "${APPLE_PRIVATE_KEY:?Set APPLE_PRIVATE_KEY to the contents of the App Store Connect .p8 file}"
: "${GITHUB_RUN_NUMBER:?Run the manual TestFlight workflow}"
: "${GITHUB_RUN_ATTEMPT:?Missing GitHub Actions run attempt}"

root="$(cd "$(dirname "$0")" && pwd)"
work="$(mktemp -d)"
trap 'rm -rf -- "$work"' EXIT
printf '%s\n' "$APPLE_PRIVATE_KEY" > "$work/AuthKey.p8"
unset APPLE_PRIVATE_KEY

auth=(
  -allowProvisioningUpdates
  -authenticationKeyPath "$work/AuthKey.p8"
  -authenticationKeyID "$APPLE_KEY_ID"
  -authenticationKeyIssuerID "$APPLE_ISSUER_ID"
)

xcodebuild -quiet archive \
  -project "$root/SilverScreen.xcodeproj" -scheme SilverScreen \
  -configuration Release -destination 'generic/platform=iOS' \
  -archivePath "$work/SilverScreen.xcarchive" \
  "${auth[@]}" DEVELOPMENT_TEAM="$APPLE_TEAM_ID" CODE_SIGN_STYLE=Automatic \
  CURRENT_PROJECT_VERSION="$GITHUB_RUN_NUMBER.$GITHUB_RUN_ATTEMPT"

options="$work/ExportOptions.plist"
plutil -create xml1 "$options"
plutil -insert method -string app-store-connect "$options"
plutil -insert destination -string upload "$options"
plutil -insert signingStyle -string automatic "$options"
plutil -insert teamID -string "$APPLE_TEAM_ID" "$options"
plutil -insert manageAppVersionAndBuildNumber -bool NO "$options"

xcodebuild -exportArchive -archivePath "$work/SilverScreen.xcarchive" \
  -exportPath "$work/export" -exportOptionsPlist "$options" "${auth[@]}"
