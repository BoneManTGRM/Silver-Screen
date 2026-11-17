# Private Silver Screen on Railway and Modal

Railway runs the Streamlit studio and its film exporter. Authenticated Modal functions run ComfyUI with Wan 2.2 5B and 14B for clips and FLUX.2 Klein for image generation and editing. The GPU has no public HTTP endpoint and scales to zero. The private Railway studio uses Caddy HTTP Basic Auth over Railway HTTPS.

## Enable the GPU

1. Use a dedicated Modal Starter workspace. Verify the active compute credit and set the workspace net spend limit to $0 in **Usage & Billing**. Verify the saved value before deploying or invoking the worker. Modal requires a valid payment method on file for GPUs. Enter payment information only on Modal's billing page.
2. Create a Modal API token for the same workspace. Railway can deploy the worker with the pre-deploy command `bash /app/deploy-renderer.sh` and a 3,600-second timeout. This builds the model images in Modal. Confirm that `silver-screen-renderer` exposes `render`, `render_wan14`, `render_wan14_text`, and `edit_image`. The 14B text and reference workflows use separate checkpoints.
3. Set `SILVER_SCREEN_MODAL_APP=silver-screen-renderer` and inject `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET` as Railway secrets for the same workspace. Use dedicated credentials for Railway and keep them out of Git and logs.

## Enable the private studio

1. Deploy the repository branch from its root with `RAILWAY_DOCKERFILE_PATH=deploy/railway/Dockerfile`. Set the sealed Railway variable `SILVER_SCREEN_PASSWORD` to a private password of at least 12 characters. The username is `silver-screen`. The application hashes the password at startup. You can also supply a Caddy bcrypt hash as `SILVER_SCREEN_PASSWORD_HASH`.
2. Configure the Railway health check to `/_stcore/health`. Add a Railway domain for testing, verify that the studio requires sign-in, and check that the logged-in Streamlit session stays connected.
3. Without a Railway volume, saved runs and cast uploads can disappear after a redeploy. For persistent use, mount a volume at `/app/runs` and ensure UID 10001 can write it. Verify the storage cost before adding the volume.
4. In **Direct Create → Video clip → ComfyUI**, generate a five-second HD clip. For image-to-video, inspect **Starting frame and render size** before submitting. Verify that the download contains motion and plays on an iPhone. Then generate three more short clips and use **Export your custom film** for a 20-second cut. The GPU invocation timeout and single-container limit bound this test setup.
5. Close the page before a render completes and reopen **Direct Create**. The page should restore the latest submitted render. Finished MP4 and PNG files are saved in the private `silver-screen-completed-clips` volume. Open a saved item to download it or confirm deletion. In **Direct Create → Image → ComfyUI**, check a text-only image and an uploaded-image edit.
6. To benchmark both Wan models, use the same starting photo, prompt, duration, frame size, sampling steps, and seed in **Direct Create**. Choose a photo with visible hands and a clear movement. The completed clip's **Render benchmark** shows the actual GPU, GPU worker time, output size, frame rate, and number of segments. Compare the downloaded clips for visible motion and continuity of the face, body, and hands.

Wan 14B outputs 16 fps at up to 1280 × 720 or 720 × 1280. Choose **2–20 seconds** in Direct Create. Clips longer than five seconds use consecutive renders, each starting from the last displayed frame of the previous segment, and export as one MP4. The render page reports segment progress. An ending photo guides the final segment. The 14B sampling settings follow the [ComfyUI Wan 2.2 workflow](https://docs.comfy.org/tutorials/video/wan/wan2_2).

### Preserve the people in a reference photo

**Preserve face and body** is enabled for photo-based Direct Create clips. It adds an appearance-preservation instruction to the original prompt and checks sampled frames for face drift, changes in visible body proportions, and loss of visible hands. Face comparison uses OpenCV's pinned YuNet and SFace models in the private worker. Reference features are held in memory and the archive keeps only review summaries. Body and hand checks use MediaPipe pose and hand landmarks. Occlusion, camera angle, and a small subject can make the result inconclusive.

Direct Create can try one more seed within its existing time limit when it detects drift. A fixed seed runs once. Flagged takes remain playable and downloadable and require a review before they can enter a custom cut. Written productions compare visible faces with the original lead photo and use their approved request limit for retries.

The writer offers 20-, 30-, and 60-second ads and 30- or 60-second movie and series trailers. Keep the writer page's draft link to reopen a pending or finished script. The studio stores those checkpoints under `/app/runs/screenplay-drafts`, so they persist across redeploys when `/app/runs` is mounted on a volume. A 60-second cut uses ten six-second Wan 5B shots. Ads can include an optional product reference and end card. Trailers close with a title. The final MP4 appears in Autonomous Studio and can be reopened with its production link while the run remains on disk.

The 48 fps clip and film export uses the generated frames and preserves their timing. It repeats frames as needed to reach 48 fps.

For Wan 2.2 14B, add an **Ending image** to guide the final pose. Use a second photo of the same subject and scene. The studio fits it to the starting frame's dimensions and previews both frames. The first and last frame workflow uses the existing Wan 2.2 14B model files.

For 14B renders Modal tries an H100 first and falls back to an L40S when needed. Test cost depends on the GPU allotted and render time. The 5B worker uses an L40S.

For a repeatable GPU check, the private Modal dashboard exposes **check_wan14_motion**. Run it with seed `341597` to render one three-second shot from the public MediaPipe pose sample. It uses the same renderer as Direct Create and allows up to 30 minutes for one full-quality attempt. Use the returned ID in **Recover a Modal render** or open the take in **Recent renders**, then review movement and hand detail. The check consumes Modal compute credits when invoked.

**check_wan14_duet** uses the same settings and accepts a seed for a three-second two-person motion check. It adapts [a reference photo by Shixart1985](https://commons.wikimedia.org/wiki/File:Two_individuals_share_a_playful_and_lively_moment_outside_a_building,_engaging_in_a_dance_as_they_smile_and_hold_hands.jpg) under [CC BY 2.0](https://creativecommons.org/licenses/by/2.0/).
