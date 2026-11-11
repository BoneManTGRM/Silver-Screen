# Silver Screen Offline

Installable photo-to-video editor. The browser decodes photos, draws the
timeline, and records the video on the device. Export uses MP4 when the
browser supports it and WebM otherwise. The editor uses a service worker to
cache its application files. Imported photos stay in the open tab and can
be reopened from Photos or Files.

Serve this folder over HTTPS to use the Home Screen and offline features.
For local development, run `python -m http.server 4173 --directory web/offline`
and open the localhost URL. No package installation or build step is needed.

On iPhone, open the hosted page in Safari and choose **Share → Add to Home
Screen**. Open the new icon online once and wait for **Ready offline**.
Import one or more photos, select camera motion and transitions, then tap
**Export video** and keep the app open while recording. Use **Save or share
video** to keep a copy.

The optional GPU test runs a small compute shader and reports the browser's
half-precision shader support. It does not download or run an image model.

When updating shell assets, change the cache version in `sw.js` to refresh
installed copies.
