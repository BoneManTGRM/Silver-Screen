# Silver Screen on one EC2 GPU

The EC2 instance runs Silver Screen and ComfyUI together. The final address is `https://saraseed.app`. Use `https://silverscreen.saraseed.app` to verify the studio before switching the main domain. The first startup downloads FLUX.1 Schnell and Wan 2.2 TI2V-5B. Models, saved productions and TLS certificates live on the instance's EBS-backed Docker volume.

## Launch the instance

1. In the EC2 console, select the project's region, **US East (Ohio)** (`us-east-2`), and launch an on-demand `g6.2xlarge` with the **Deep Learning Base OSS Nvidia Driver GPU AMI (Ubuntu 24.04)** for x86_64. Choose a **150 GiB gp3 root disk**. The regional **Running On-Demand G and VT instances** quota must cover 8 vCPUs for this instance. Check the quota before launching.
2. Give the instance a public IPv4 address. Allow inbound TCP `80` and `443` for HTTPS and certificate validation. Keep administrative access private, using Session Manager if configured. Keep ports `8188` and `8501` closed to the internet.
3. In Cloudflare, open `saraseed.app` and choose **DNS → Records**. Check for an existing `silverscreen` record, then add an **A** record named `silverscreen` with the instance's public IPv4 address and set it to **DNS only** for Caddy's HTTPS setup. Use this address for the deployment test. An automatically assigned public IP can change when the instance stops and restarts. An Elastic IP keeps the address stable and incurs its own hourly charge.
4. On the host, verify `nvidia-smi`, `sudo docker compose version`, and `sudo docker info`. Docker must be configured for the NVIDIA Container Toolkit, and its data directory must be on the EBS root disk to retain models and renders between stops. Check out the deployment revision of Silver Screen on the instance and run `mkdir -p runs` from the repository root so Docker can mount the writable production volume under the read-only source tree.

## Start Silver Screen

From the repository root, create a private environment file in `deploy/ec2`:

```dotenv
SILVER_SCREEN_PUBLIC_HOST=silverscreen.saraseed.app
SILVER_SCREEN_PASSWORD='replace-with-your-own-private-password'
```

Save it as `deploy/ec2/.env` with mode `600`. Use a password at least 11 characters long. The hostname should be a DNS name without a URL scheme or path.

```bash
sudo docker compose --env-file deploy/ec2/.env -f deploy/ec2/compose.yaml up -d --build
sudo docker compose --env-file deploy/ec2/.env -f deploy/ec2/compose.yaml logs -f studio
```

Open `https://silverscreen.saraseed.app` in Safari and sign in as `silver-screen`. In **Direct Create**, render one image and one short moving clip, then play and download the clip from your iPhone. Confirm the landing page requires sign-in before you switch the main domain.

For the two-lead starter, choose **Video clip**, **ComfyUI**, and **Last Train Out · 60-second action short**, then enable **Use this shot list**. Upload individual photos for solo shots and one photo containing both adults for shared shots. Choose the starting frame for each shot, edit its prompt, then render. Open **Record dialogue and smooth this clip** to capture your two performances separately, place them on the shot timeline, and preview the edit. **Export Last Train Out** assembles the selected shots in story order and trims the final shot to the chosen runtime. To make a different film in **Direct Create**, render your own shots and choose **Add clip to custom film** after each shot or dialogue edit. Under **Export your custom film**, select the clips in playback order and choose the runtime and output size. Download your MP4 before closing the session. For longer projects use **Star Vehicle Studio → Render length → Custom runtime** and **Voice Studio** for finished audio tracks.

The bundled Wan workflow supports clip lengths from 2 to 12 seconds. Open **Camera format and render settings** to choose landscape or portrait, standard or HD, sampling steps, an optional repeatable seed, and anything you want the model to avoid. Higher resolutions, more sampling steps, and longer shots take more GPU time. Film export supports landscape and portrait HD. The 48 fps export interpolates motion at normal playback speed. The render timer includes queue wait and download. Check it on the first live GPU test before planning production time.

Use **Cast Library** to save solo and shared reference photos and named voice takes. **Direct Create** can reuse them in any new clip. **Star Vehicle Studio** can add saved photos to its identity references, and **Voice Studio** can add saved takes in clip order. The private Docker volume keeps the library across EC2 stops and restarts. Saved recordings play the words recorded in that take.

In **Cast Library → Voice clones**, each speaker can save their own short recording and preview new lines in English or Spanish. In **Direct Create**, type dialogue under **Record dialogue and smooth this clip**. For longer saved productions, choose **Private cast voices** in **Professional Script Sync** and assign each script speaker to a saved voice. Chatterbox runs in a separate CPU container on the same EC2 instance. Its first request downloads public model weights, which are cached in the `voice-models` volume. Photos, sample recordings, and scripts are processed by the studio and its private worker. The voice service does not publish a port to the internet.

## Switch the main domain to Silver Screen

1. Record the current Cloudflare records at `@` that route web traffic, including type, target, proxy setting and TTL. Keep those values for rollback.
2. In `deploy/ec2/.env`, set `SILVER_SCREEN_PUBLIC_HOST=saraseed.app`. In Cloudflare, switch the web records for `@` to a **DNS only A record** pointing to the verified Silver Screen EC2 address. Remove any superseded web-only `A`, `AAAA`, or `CNAME` record at `@` so browsers reach the same host over IPv4 and IPv6. Leave mail and verification records in place.
3. Run `sudo docker compose --env-file deploy/ec2/.env -f deploy/ec2/compose.yaml up -d studio`. Check `https://saraseed.app` for valid HTTPS, an authentication challenge, and the Silver Screen page after login. Play a finished clip in Safari.
4. If the switch fails, restore the recorded Cloudflare values and set `SILVER_SCREEN_PUBLIC_HOST=silverscreen.saraseed.app` again. Run the same Compose command to reopen the test address.

## Pause billing for compute

After the render finishes, select **Stop instance** in EC2. Stopping only the Docker container leaves EC2 compute billing active. The EBS disk continues to incur storage charges while the instance is stopped. Download or back up your work before terminating the instance or deleting its EBS disk.
