# Private Silver Screen on Railway and Modal

Railway runs the existing Streamlit studio and its film exporter. An authenticated Modal function runs ComfyUI with Wan 2.2 TI2V-5B and FLUX.1 Schnell. The GPU has no public HTTP endpoint and scales to zero. The private Railway studio uses Caddy HTTP Basic Auth over Railway HTTPS. Keep the existing site and DNS in place during verification.

## Enable the GPU

1. Use a dedicated Modal Starter workspace. Verify the active compute credit and set the workspace net spend limit to $0 in **Usage & Billing**. Verify the saved value before deploying or invoking the worker. Modal requires a valid payment method on file for GPUs. Enter payment information only on Modal's billing page.
2. Create a Modal API token for the same workspace. Railway can deploy the worker with the pre-deploy command `bash /app/deploy-renderer.sh` and a 3,600-second timeout. This builds the model image in Modal. Confirm that the app named `silver-screen-renderer` exposes `render`.
3. Set `SILVER_SCREEN_MODAL_APP=silver-screen-renderer` and inject `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET` as Railway secrets for the same workspace. Use dedicated credentials for Railway and keep them out of Git and logs.

## Enable the private studio

1. Deploy the repository branch from its root with `RAILWAY_DOCKERFILE_PATH=deploy/railway/Dockerfile`. Set the sealed Railway variable `SILVER_SCREEN_PASSWORD` to a private password of at least 12 characters. The username is `silver-screen`. The application hashes the password at startup. You can also supply a Caddy bcrypt hash as `SILVER_SCREEN_PASSWORD_HASH`.
2. Configure the Railway health check to `/_stcore/health`. Add a Railway domain for testing, verify that the studio requires sign-in, and check that the logged-in Streamlit session stays connected.
3. Without a Railway volume, saved runs and cast uploads can disappear after a redeploy. For persistent use, mount a volume at `/app/runs` and ensure UID 10001 can write it. Verify the storage cost before adding the volume.
4. In **Direct Create → Video clip → ComfyUI**, start with one five-second standard-resolution prompt. Verify that the download contains motion and plays on an iPhone. Then generate three more short clips and use **Export your custom film** for a 20-second cut. The GPU invocation timeout and single-container limit bound this test setup. Increase concurrency or clip size only after verifying the first render and its metered usage.
