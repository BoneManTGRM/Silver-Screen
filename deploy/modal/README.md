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
