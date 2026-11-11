# Silver Screen Offline on iPhone

Silver Screen Offline is a Draw Things script for local image and short clip
generation. Draw Things provides the model runtime and export controls. After
the initial app and model downloads, you can generate with Wi-Fi and cellular
data off.

## One-time setup

1. Install [Draw Things from the Mexico App Store](https://apps.apple.com/mx/app/draw-things-offline-ai-art/id6444050820).
2. In Draw Things, download a local image model and, for clips, a local video
   model your phone supports. Start with the app's recommended settings.
3. Download [silver-screen-offline.js](silver-screen-offline.js). In Draw
   Things, open the script editor and add **Silver Screen Offline**. Use its
   import option if available, or copy the script text into the editor and save it.

## Make something offline

- **Image** — select your downloaded image model and start on a blank canvas.
  Run **Silver Screen Offline**, enter your prompt, and tap **Generate**.
- **Clip** — select a downloaded image-to-video model, load your starting image
  in Draw Things, and start with the shortest clip and lowest resolution
  the model offers. Run the same script and describe the motion you want.

Use the Draw Things export controls to save the result. The script uses the
active model, frame count, canvas, and other generation settings. If Draw
Things is set to cloud compute, switch to local compute first. To verify
offline operation, try one image and one short clip with Wi-Fi and cellular
data turned off.

Silver Screen's full movie planner, shot queue, and movie assembly are
separate from this on-device workflow.
