# Private Silver Screen on Railway and Modal

Railway runs the existing Streamlit studio and its film exporter. Authenticated Modal functions run ComfyUI with Wan 2.2 5B, Wan 2.2 14B image-to-video, and FLUX.1 Schnell. The GPU has no public HTTP endpoint and scales to zero. The private Railway studio uses Caddy HTTP Basic Auth over Railway HTTPS. Keep the existing site and DNS in place during verification.

## Enable the GPU

1. Use a dedicated Modal Starter workspace. Verify the active compute credit and set the workspace net spend limit to $0 in **Usage & Billing**. Verify the saved value before deploying or invoking the worker. Modal requires a valid payment method on file for GPUs. Enter payment information only on Modal's billing page.
2. Create a Modal API token for the same workspace. Railway can deploy the worker with the pre-deploy command `bash /app/deploy-renderer.sh` and a 3,600-second timeout. This builds the model images in Modal. Confirm that the app named `silver-screen-renderer` exposes `render` and `render_wan14`. The 14B image downloads about 29 GB of additional model weights on its first build.
3. Set `SILVER_SCREEN_MODAL_APP=silver-screen-renderer` and inject `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET` as Railway secrets for the same workspace. Use dedicated credentials for Railway and keep them out of Git and logs.

## Enable the private studio

1. Deploy the repository branch from its root with `RAILWAY_DOCKERFILE_PATH=deploy/railway/Dockerfile`. Set the sealed Railway variable `SILVER_SCREEN_PASSWORD` to a private password of at least 12 characters. The username is `silver-screen`. The application hashes the password at startup. You can also supply a Caddy bcrypt hash as `SILVER_SCREEN_PASSWORD_HASH`.
2. Configure the Railway health check to `/_stcore/health`. Add a Railway domain for testing, verify that the studio requires sign-in, and check that the logged-in Streamlit session stays connected.
3. Without a Railway volume, saved runs and cast uploads can disappear after a redeploy. For persistent use, mount a volume at `/app/runs` and ensure UID 10001 can write it. Verify the storage cost before adding the volume.
4. In **Direct Create → Video clip → ComfyUI**, generate a five-second HD clip. For image-to-video, inspect **Starting frame and render size** before submitting. Verify that the download contains motion and plays on an iPhone. Then generate three more short clips and use **Export your custom film** for a 20-second cut. The GPU invocation timeout and single-container limit bound this test setup.
5. The Modal worker stores completed Direct Create MP4s in its private `silver-screen-completed-clips` volume. Close the page before the test render completes, then reopen **Direct Create → Saved clips** and play the finished result. The original render ID also works in **Recover a Modal render** after the temporary call result expires.
6. For the quality comparison, use the same starting photo, prompt, duration, and seed in **Render model** for both Wan variants. Choose a photo with visible hands and a single clear movement. The 14B mode uses both Wan denoisers, outputs 16 fps at up to 1280 × 720 or 720 × 1280, and supports two- to five-second shots. Compare sharpness on the moving subject, hand visibility and shape, and motion in the downloaded clips. The 14B render can take longer and uses more GPU time per shot. Its sampling settings follow the [ComfyUI Wan 2.2 workflow](https://docs.comfy.org/tutorials/video/wan/wan2_2).

### Review hand continuity

For a photo-based Direct Create render, the Modal worker samples the clip and looks for sustained loss of hand landmarks near a wrist that was visible in the starting photo. The check can follow one or two performers and can distinguish separated wrists from likely overlap or occlusion. The worker can try one additional seed if it has enough time left. The studio keeps the result available for playback and download, and asks for review before including a flagged take in a film. Automated film production checks photo-based character shots and keeps flagged attempts out of the cut. Those attempts use the film queue's existing retry budget. The check can be inconclusive for overlapping or crowded scenes, and it can miss deformed fingers.

The 48 fps clip and film export uses the generated frames and preserves their timing. It repeats frames as needed to reach 48 fps.

For Wan 2.2 14B, add an **Ending image** to guide the final pose. Use a second photo of the same subject and scene. The studio fits it to the starting frame's dimensions and previews both frames. The first and last frame workflow uses the existing Wan 2.2 14B model files.

For 14B renders Modal tries an H100 first and falls back to an L40S when needed. Test cost depends on the GPU allotted and render time. The 5B worker uses an L40S.

For a repeatable GPU check, the private Modal dashboard exposes **check_wan14_motion**. Run it with seed `341597` to render one three-second shot from the public MediaPipe pose sample. It uses the same renderer as Direct Create and allows up to 30 minutes for one full-quality attempt. Use the returned ID in **Recover a Modal render** or open the take in **Saved clips**, then review movement and hand detail. The check consumes Modal compute credits when invoked.

**check_wan14_duet** uses the same settings and accepts a seed for a three-second two-person motion check. It adapts [a reference photo by Shixart1985](https://commons.wikimedia.org/wiki/File:Two_individuals_share_a_playful_and_lively_moment_outside_a_building,_engaging_in_a_dance_as_they_smile_and_hold_hands.jpg) under [CC BY 2.0](https://creativecommons.org/licenses/by/2.0/).

### Preview motion-guided shots

Deploy the worker to a test app with `SILVER_SCREEN_RENDERER_DEPLOY_APP=silver-screen-motion-preview modal deploy deploy/modal/worker.py`. In the matching private studio, set `SILVER_SCREEN_MODAL_APP=silver-screen-motion-preview` and `SILVER_SCREEN_MOTION_REFERENCE=1`. Open Direct Create, select the 14B video model, add a starting photo for the look, and upload a motion reference. Use a 2- to 5-second MP4 or MOV up to 12 MiB, at least as long as the shot. Matching the look image and video framing gives the two inputs compatible compositions.

The worker samples the reference by timestamp at Wan's native 16 fps, pads to fit the canvas without cropping, and uses edge conditioning for the whole frame. It drops source audio. Choose a five-second test with two visible people and check that both performers follow the source timing, retain distinct faces and hands, and keep their feet steady during pauses. Check playback again from Saved clips on an iPhone before enabling the option in production.

### Preview prompt-only HD shots

Deploy the worker to a test app and point the private test studio at that app as described above. Set `SILVER_SCREEN_TEXT_14B=1` on the test studio. In Direct Create, choose Video clip, enter a prompt, and select Generate. With no starting photo, the default uses the dedicated Wan 2.2 14B text-to-video model at five seconds and 1280 × 720. Keep the same prompt, seed, and duration when comparing against Wan 2.2 5B. Review movement and anatomy at normal playback speed and confirm the original clip reopens from Saved clips.
