# Silver Screen for iPhone

The SwiftUI app has its own local renderer via MediaGenerationKit. It uses
Stable Diffusion 1.5 for 512 × 512 PNGs and the quantized Wan 2.1 1.3B model
for short, silent MP4 clips. The initial clip preset uses 17 frames at
512 × 320 and 16 fps to keep the first device trial small. Draw Things,
RunPod, and Railway are separate from this app.

The app downloads the selected model and its supporting weights when you tap
**Download model**. Generation uses the SDK's local backend. The app keeps
renders in its Documents directory and offers the iOS share sheet to save
the most recent one. The initial build focuses on prompt-to-image and
prompt-to-clip, independent of Silver Screen's movie planner.

## Build and test

On a Mac with Xcode 26.3 or newer and [XcodeGen](https://github.com/yonaskolb/XcodeGen), run:

```sh
xcodegen generate --spec ios/project.yml
xcodebuild -project ios/SilverScreen.xcodeproj -scheme SilverScreen \
  -destination 'generic/platform=iOS' CODE_SIGNING_ALLOWED=NO build
```

The iPhone build workflow runs the unsigned device build in GitHub Actions.
It produces compile evidence. An installable build needs Apple signing.

For the first device test, install a signed build and download both models
over Wi-Fi. Quit and reopen the app with airplane mode enabled and Wi-Fi
disabled. Generate a PNG and MP4, verify the clip plays and contains motion,
and save both through the share sheet. Measure memory, render time, and
available storage on the target iPhone before raising the video preset.

## Install through TestFlight

Review the SDK and model license notices before distribution. In your Apple
Developer team, register `com.bonemantgrm.silverscreen` and create its iOS app
record in App Store Connect. Create a team App Store Connect API key with
permission to sign and upload builds for that app.

In the GitHub repository's Actions settings, save `APPLE_TEAM_ID` as a
variable. Save `APPLE_KEY_ID`, `APPLE_ISSUER_ID`, and `APPLE_PRIVATE_KEY` as
secrets. The last secret is the full contents of the Apple-issued `.p8` file.

After the workflow is on the default branch, run **Upload Silver Screen to
TestFlight** from GitHub Actions. It creates a signed archive and uploads it
to App Store Connect. Once Apple processes it, add your Apple account to a
TestFlight testing group and install the app from the TestFlight invitation.
This upload has not been verified with a live Apple Developer account.
