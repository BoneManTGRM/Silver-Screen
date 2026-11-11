import { drawFrame, formats, recordClip, recorderType } from "./render.mjs";
import { testGPU } from "./gpu.mjs";

const get = id => document.getElementById(id);
const canvas = get("canvas");
const input = get("photos");
const photoList = get("photo-list");
const status = get("status");
const photos = [];
const maximumPhotos = 10;
let busy = false;
let playing = false;
let animation;
let output;
let outputURL;

function settings() {
  return {
    seconds: Number(get("seconds").value),
    motion: get("motion").value,
    crossfade: get("transition").value === "fade",
  };
}

function stopPreview() {
  playing = false;
  cancelAnimationFrame(animation);
  get("preview").textContent = "Play preview";
}

function refresh() {
  const hasPhotos = photos.length > 0;
  get("canvas").hidden = !hasPhotos;
  get("empty").hidden = hasPhotos;
  get("no-photos").hidden = hasPhotos;
  get("preview").disabled = busy || !hasPhotos;
  get("export").disabled = busy || !hasPhotos || !recorderType();
  get("settings").disabled = busy;
  input.disabled = busy || photos.length >= maximumPhotos;
  document.querySelector(".photo-button").setAttribute("aria-disabled", String(input.disabled));
  get("duration").textContent = hasPhotos ? `${photos.length * settings().seconds} sec` : "No photos";
  photoList.replaceChildren(...photos.map((photo, index) => {
    const row = document.createElement("li");
    const thumb = document.createElement("img");
    thumb.src = photo.thumb;
    thumb.alt = "";
    const name = document.createElement("span");
    name.className = "photo-name";
    name.textContent = photo.name;
    const action = (symbol, label, disabled, callback) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = symbol;
      button.setAttribute("aria-label", `${label} photo ${index + 1}`);
      button.disabled = busy || disabled;
      button.addEventListener("click", callback);
      return button;
    };
    row.append(thumb, name,
      action("↑", "Move earlier", index === 0, () => swap(index, index - 1)),
      action("↓", "Move later", index === photos.length - 1, () => swap(index, index + 1)),
      action("×", "Remove", false, () => {
        photos.splice(index, 1);
        edit();
      }),
    );
    return row;
  }));
  if (!busy && hasPhotos) {
    const [width, height] = formats[get("format").value];
    if (canvas.width !== width) canvas.width = width;
    if (canvas.height !== height) canvas.height = height;
    drawFrame(canvas, photos, settings(), 0);
  }
}

function edit() {
  stopPreview();
  refresh();
  status.textContent = photos.length && !recorderType()
    ? "Video export is unavailable in this browser. You can still preview the clip."
    : "";
}

function swap(first, second) {
  [photos[first], photos[second]] = [photos[second], photos[first]];
  edit();
}

async function decodePhoto(file) {
  const url = URL.createObjectURL(file);
  try {
    const image = new Image();
    image.src = url;
    await image.decode();
    const scale = Math.min(1, 1600 / Math.max(image.naturalWidth, image.naturalHeight));
    const source = document.createElement("canvas");
    source.width = Math.max(1, Math.round(image.naturalWidth * scale));
    source.height = Math.max(1, Math.round(image.naturalHeight * scale));
    source.getContext("2d", { alpha: false }).drawImage(image, 0, 0, source.width, source.height);
    const thumbnail = document.createElement("canvas");
    thumbnail.width = 88;
    thumbnail.height = 88;
    const side = Math.min(source.width, source.height);
    thumbnail.getContext("2d", { alpha: false }).drawImage(
      source, (source.width - side) / 2, (source.height - side) / 2, side, side, 0, 0, 88, 88,
    );
    return { name: file.name, source, thumb: thumbnail.toDataURL("image/jpeg", .8) };
  } finally {
    URL.revokeObjectURL(url);
  }
}

input.addEventListener("change", async () => {
  const picked = Array.from(input.files);
  input.value = "";
  if (!picked.length || busy) return;
  stopPreview();
  busy = true;
  refresh();
  status.textContent = "Opening photos…";
  const room = maximumPhotos - photos.length;
  let failed = 0;
  try {
    for (const file of picked.slice(0, room)) {
      try { photos.push(await decodePhoto(file)); }
      catch { failed++; }
    }
  } finally {
    busy = false;
    edit();
    const messages = [status.textContent];
    if (picked.length > room) messages.push(`A clip can contain up to ${maximumPhotos} photos.`);
    if (failed) messages.push(`Could not open ${failed} photo${failed === 1 ? "" : "s"}. Try JPEG or PNG.`);
    status.textContent = messages.filter(Boolean).join(" ");
  }
});

get("settings").addEventListener("change", edit);
get("preview").addEventListener("click", () => {
  if (playing) {
    stopPreview();
    return;
  }
  const config = settings();
  const start = performance.now();
  playing = true;
  get("preview").textContent = "Stop preview";
  const draw = now => {
    const elapsed = Math.max(0, (now - start) / 1000);
    const duration = config.seconds * photos.length;
    drawFrame(canvas, photos, config, Math.min(elapsed, duration));
    if (elapsed >= duration) stopPreview();
    else animation = requestAnimationFrame(draw);
  };
  animation = requestAnimationFrame(draw);
});

get("export").addEventListener("click", async () => {
  stopPreview();
  const config = settings();
  const progress = get("progress");
  busy = true;
  refresh();
  progress.value = 0;
  progress.hidden = false;
  status.textContent = "Exporting. Keep Silver Screen open.";
  try {
    const blob = await recordClip(canvas, photos, config, value => { progress.value = value; });
    const extension = blob.type.includes("mp4") ? "mp4" : "webm";
    const type = extension === "mp4" ? "video/mp4" : "video/webm";
    output = new File([blob], `silver-screen-${Date.now()}.${extension}`, { type });
    if (outputURL) URL.revokeObjectURL(outputURL);
    outputURL = URL.createObjectURL(output);
    get("player").src = outputURL;
    get("result").hidden = false;
    status.textContent = `Your ${extension.toUpperCase()} is ready.`;
  } catch (error) {
    status.textContent = error.message || "Could not export your video.";
  } finally {
    busy = false;
    progress.hidden = true;
    refresh();
  }
});

get("save").addEventListener("click", async () => {
  if (!output) return;
  if (navigator.canShare?.({ files: [output] })) {
    try {
      await navigator.share({ files: [output] });
      return;
    } catch (error) {
      if (error.name === "AbortError") return;
    }
  }
  const link = document.createElement("a");
  link.href = outputURL;
  link.download = output.name;
  link.click();
});

get("gpu-test").addEventListener("click", async () => {
  const button = get("gpu-test");
  const output = get("gpu-status");
  button.disabled = true;
  output.textContent = "Testing local GPU compute…";
  try {
    const f16 = await testGPU();
    output.textContent = `Local GPU compute passed. Half-precision shaders: ${f16 ? "available" : "unavailable"}. Model compatibility still needs a separate test.`;
  } catch (error) {
    output.textContent = error.message || "The GPU test failed.";
  } finally {
    button.disabled = false;
  }
});

async function installOffline() {
  const label = get("offline");
  if (!navigator.serviceWorker) {
    label.textContent = "Offline install unavailable";
    return;
  }
  try {
    await navigator.serviceWorker.register("./sw.js");
    const timeout = setTimeout(() => { label.textContent = "Offline setup pending"; }, 8000);
    await navigator.serviceWorker.ready;
    clearTimeout(timeout);
    label.dataset.ready = "true";
    label.textContent = "Ready offline";
  } catch {
    label.textContent = "Offline setup unavailable";
  }
}

if (navigator.standalone || matchMedia("(display-mode: standalone)").matches) {
  get("install").hidden = true;
}
refresh();
installOffline();
