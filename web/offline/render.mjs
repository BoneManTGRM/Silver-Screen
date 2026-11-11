export const formats = {
  portrait: [720, 1280],
  landscape: [1280, 720],
  square: [720, 720],
};

export function frameAt(time, count, seconds, crossfade) {
  const end = count * seconds;
  const position = Math.max(0, Math.min(time, end));
  const index = Math.min(count - 1, Math.floor(position / seconds));
  const elapsed = position - index * seconds;
  const fadeTime = Math.min(.6, seconds / 3);
  return {
    index,
    progress: Math.min(1, elapsed / seconds),
    mix: crossfade && index < count - 1
      ? Math.max(0, (elapsed - (seconds - fadeTime)) / fadeTime)
      : 0,
  };
}

function cover(context, photo, width, height, motion, progress) {
  const ease = progress * progress * (3 - 2 * progress);
  let zoom = 1;
  if (motion === "push") zoom = 1 + ease * .14;
  if (motion === "pull") zoom = 1 + (1 - ease) * .14;
  if (motion === "pan") zoom = 1.15;
  const scale = Math.max(width / photo.width, height / photo.height) * zoom;
  const w = photo.width * scale;
  const h = photo.height * scale;
  const x = (width - w) * (motion === "pan" ? .16 + .68 * ease : .5);
  context.drawImage(photo, x, (height - h) / 2, w, h);
}

export function drawFrame(canvas, photos, settings, time) {
  const context = canvas.getContext("2d", { alpha: false });
  const { width, height } = canvas;
  const frame = frameAt(time, photos.length, settings.seconds, settings.crossfade);
  context.globalAlpha = 1;
  cover(context, photos[frame.index].source, width, height, settings.motion, frame.progress);
  if (frame.mix > 0) {
    context.globalAlpha = frame.mix;
    cover(context, photos[frame.index + 1].source, width, height, settings.motion, 0);
    context.globalAlpha = 1;
  }
}

export function recorderType() {
  if (!globalThis.MediaRecorder || !globalThis.HTMLCanvasElement?.prototype.captureStream) return "";
  const types = ["video/mp4;codecs=avc1.42001f", "video/mp4", "video/webm;codecs=vp8", "video/webm"];
  return types.find(type => MediaRecorder.isTypeSupported(type)) || "";
}

export async function recordClip(canvas, photos, settings, onProgress) {
  const mimeType = recorderType();
  if (!mimeType) throw new Error("This browser can preview clips but cannot export them.");
  if (!photos.length) throw new Error("Add a photo before exporting.");

  drawFrame(canvas, photos, settings, 0);
  const stream = canvas.captureStream(24);
  let animation;
  let recorder;
  let interrupted = false;
  const stop = () => {
    if (recorder?.state === "recording") recorder.stop();
  };
  const visibilityChanged = () => {
    if (document.hidden) {
      interrupted = true;
      stop();
    }
  };
  try {
    recorder = new MediaRecorder(stream, { mimeType, videoBitsPerSecond: 3_000_000 });
    const chunks = [];
    const complete = new Promise((resolve, reject) => {
      recorder.ondataavailable = event => {
        if (event.data.size) chunks.push(event.data);
      };
      recorder.onerror = () => reject(new Error("Video recording failed. Try a shorter clip."));
      recorder.onstop = () => {
        if (interrupted) {
          reject(new Error("The export paused when you left the app. Open it and export again."));
          return;
        }
        if (!chunks.length) {
          reject(new Error("No video was recorded. Try exporting again."));
          return;
        }
        resolve(new Blob(chunks, { type: recorder.mimeType || mimeType }));
      };
    });

    document.addEventListener("visibilitychange", visibilityChanged);
    const duration = photos.length * settings.seconds;
    let start;
    const render = now => {
      start ??= now;
      const elapsed = (now - start) / 1000;
      drawFrame(canvas, photos, settings, Math.min(duration, elapsed));
      onProgress(Math.min(1, elapsed / duration));
      if (elapsed >= duration) animation = requestAnimationFrame(stop);
      else animation = requestAnimationFrame(render);
    };
    recorder.start();
    animation = requestAnimationFrame(render);
    return await complete;
  } finally {
    cancelAnimationFrame(animation);
    document.removeEventListener("visibilitychange", visibilityChanged);
    stop();
    stream.getTracks().forEach(track => track.stop());
  }
}
