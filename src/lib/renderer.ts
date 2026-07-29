import type { FilmProject } from "@/lib/types";
import { STUDIO } from "@/lib/types";

export type RenderProgress = (pct: number, label: string) => void;

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = reject;
    img.src = src;
  });
}

function pickMime(): string {
  const candidates = [
    "video/webm;codecs=vp9",
    "video/webm;codecs=vp8",
    "video/webm",
  ];
  for (const c of candidates) {
    if (typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported(c)) {
      return c;
    }
  }
  return "video/webm";
}

type FrameClip = {
  image?: HTMLImageElement;
  duration: number;
  dialogue?: string;
  slugline: string;
  isTitle: boolean;
  isAct: boolean;
  title?: string;
  subtitle?: string;
};

async function recordClips(
  clips: FrameClip[],
  onProgress: RenderProgress,
  labelPrefix: string,
): Promise<string> {
  const width = 1280;
  const height = 720;
  const fps = 24;
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d")!;

  const stream = canvas.captureStream(fps);
  const mime = pickMime();
  const recorder = new MediaRecorder(stream, {
    mimeType: mime,
    videoBitsPerSecond: 4_000_000,
  });
  const chunks: BlobPart[] = [];
  recorder.ondataavailable = (e) => {
    if (e.data.size > 0) chunks.push(e.data);
  };

  const done = new Promise<Blob>((resolve) => {
    recorder.onstop = () => resolve(new Blob(chunks, { type: mime.split(";")[0] }));
  });

  const totalDuration = clips.reduce((s, c) => s + c.duration, 0);
  recorder.start(100);
  onProgress(2, `${labelPrefix} · opening reel…`);

  let elapsed = 0;
  for (const clip of clips) {
    const frames = Math.max(1, Math.round(clip.duration * fps));
    for (let f = 0; f < frames; f++) {
      const t = f / frames;
      const ken = 1 + t * 0.06;
      ctx.fillStyle = "#050506";
      ctx.fillRect(0, 0, width, height);

      if (clip.image) {
        const iw = clip.image.width;
        const ih = clip.image.height;
        const scale = Math.max(width / iw, height / ih) * ken;
        const dw = iw * scale;
        const dh = ih * scale;
        const dx = (width - dw) / 2 - t * 20;
        const dy = (height - dh) / 2;
        ctx.drawImage(clip.image, dx, dy, dw, dh);
      } else {
        const g = ctx.createLinearGradient(0, 0, width, height);
        g.addColorStop(0, "#0c0c10");
        g.addColorStop(1, "#1a1814");
        ctx.fillStyle = g;
        ctx.fillRect(0, 0, width, height);
      }

      ctx.fillStyle = "#000";
      ctx.fillRect(0, 0, width, 48);
      ctx.fillRect(0, height - 48, width, 48);

      if ((clip.isTitle || clip.isAct) && clip.title) {
        ctx.fillStyle = "rgba(0,0,0,0.4)";
        ctx.fillRect(0, 0, width, height);
        ctx.fillStyle = "#e8e2d4";
        ctx.font = `500 ${clip.isAct ? 44 : 56}px "Cormorant Garamond", Georgia, serif`;
        ctx.textAlign = "center";
        ctx.globalAlpha = Math.min(1, t * 3);
        ctx.fillText(clip.title.toUpperCase(), width / 2, height / 2);
        ctx.font = `400 14px "DM Sans", system-ui, sans-serif`;
        ctx.fillStyle = "rgba(232,226,212,0.7)";
        ctx.fillText(
          clip.subtitle ?? STUDIO.credit,
          width / 2,
          height / 2 + 40,
        );
        ctx.globalAlpha = 1;
      }

      if (clip.dialogue) {
        const alpha = t < 0.1 ? t / 0.1 : t > 0.85 ? (1 - t) / 0.15 : 1;
        ctx.globalAlpha = Math.max(0, alpha);
        ctx.fillStyle = "rgba(0,0,0,0.55)";
        const boxW = Math.min(900, width - 80);
        ctx.fillRect((width - boxW) / 2, height - 140, boxW, 64);
        ctx.fillStyle = "#f0ebe0";
        ctx.font = `400 20px "DM Sans", system-ui, sans-serif`;
        ctx.textAlign = "center";
        ctx.fillText(clip.dialogue, width / 2, height - 100, boxW - 40);
        ctx.globalAlpha = 1;
      }

      ctx.fillStyle = "rgba(232,226,212,0.45)";
      ctx.font = `500 11px "DM Sans", system-ui, sans-serif`;
      ctx.textAlign = "left";
      ctx.fillText(clip.slugline, 28, 30);
      ctx.textAlign = "right";
      ctx.fillText("REPARODYNAMICS · TGRM", width - 28, 30);

      elapsed += 1 / fps;
      const pct = Math.min(98, Math.round((elapsed / totalDuration) * 100));
      if (f % 6 === 0) onProgress(pct, `${labelPrefix} · ${clip.slugline}`);

      await new Promise((r) => requestAnimationFrame(() => r(null)));
    }
  }

  onProgress(99, `${labelPrefix} · finalizing…`);
  recorder.stop();
  stream.getTracks().forEach((tr) => tr.stop());
  const blob = await done;
  onProgress(100, `${labelPrefix} · complete`);
  return URL.createObjectURL(blob);
}

async function scenesToClips(
  project: FilmProject,
  sceneFilter?: (sceneId: string) => boolean,
): Promise<FrameClip[]> {
  const clips: FrameClip[] = [];
  for (const scene of project.scenes) {
    if (sceneFilter && !sceneFilter(scene.id)) continue;
    for (const shot of scene.shots) {
      let image: HTMLImageElement | undefined;
      if (shot.frameDataUrl) {
        try {
          image = await loadImage(shot.frameDataUrl);
        } catch {
          image = undefined;
        }
      }
      clips.push({
        image,
        duration: shot.durationSec,
        dialogue: shot.dialogue,
        slugline: scene.slugline,
        isTitle: shot.type === "title",
        isAct: shot.type === "actcard",
        title: shot.type === "title" ? project.title : shot.type === "actcard" ? `Act ${scene.act}` : undefined,
        subtitle:
          shot.type === "title"
            ? `${STUDIO.credit} · ${project.targetMinutes}m ${project.format}`
            : shot.type === "actcard"
              ? project.acts.find((a) => a.number === scene.act)?.title
              : undefined,
      });
    }
  }
  clips.push({
    duration: 2.8,
    slugline: "END CARD",
    isTitle: true,
    isAct: false,
    title: project.title,
    subtitle: STUDIO.credit,
  });
  return clips;
}

/** Hero reel — all key beats (production sizzle / full short). */
export async function renderFilm(
  project: FilmProject,
  onProgress: RenderProgress,
): Promise<string> {
  const clips = await scenesToClips(project);
  return recordClips(clips, onProgress, "TGRM hero reel");
}

/** Chapter reel — one chapter of a full-length film. */
export async function renderChapter(
  project: FilmProject,
  chapterNumber: number,
  onProgress: RenderProgress,
): Promise<string> {
  const chapter = project.chapters.find((c) => c.number === chapterNumber);
  const ids = new Set(chapter?.sceneIds ?? []);
  const clips = await scenesToClips(project, (id) => ids.has(id));
  if (clips.length <= 1) {
    // chapter empty — still produce end card
    clips.unshift({
      duration: 2,
      slugline: `CHAPTER ${chapterNumber}`,
      isTitle: true,
      isAct: false,
      title: chapter?.title ?? `Chapter ${chapterNumber}`,
      subtitle: STUDIO.credit,
    });
  }
  return recordClips(clips, onProgress, `Chapter ${chapterNumber}`);
}

export function buildNftMetadata(project: FilmProject) {
  const totalShots = project.scenes.reduce((n, s) => n + s.shots.length, 0);
  const reelSec = project.scenes.reduce((n, s) => n + s.durationSec, 0);
  return {
    name: project.title,
    description: `${project.logline || project.premise}\n\n${STUDIO.credit}`,
    external_url: STUDIO.github,
    image: project.posterDataUrl,
    animation_url: project.renderUrl,
    collection: "Silver-Screen · Reparodynamics",
    studio: STUDIO.name,
    attributes: [
      { trait_type: "Studio", value: "Reparodynamics" },
      { trait_type: "Pipeline", value: "TGRM" },
      { trait_type: "Metric", value: "RYE" },
      { trait_type: "Stability Layer", value: "MSIL" },
      { trait_type: "Genre", value: project.genre },
      { trait_type: "Tone", value: project.tone },
      { trait_type: "Format", value: project.format },
      { trait_type: "Target Minutes", value: project.targetMinutes },
      { trait_type: "Acts", value: project.acts.length },
      { trait_type: "Chapters", value: project.chapters.length },
      { trait_type: "Scenes", value: project.scenes.length },
      { trait_type: "Shots", value: totalShots },
      { trait_type: "Hero Reel Sec", value: Math.round(reelSec) },
      { trait_type: "RYE", value: project.rye?.rye ?? 0 },
      { trait_type: "Stability Index", value: project.msil?.stabilityIndex ?? 0 },
      { trait_type: "MSIL Verdict", value: project.msil?.verdict ?? "unknown" },
      { trait_type: "Tau", value: 0.6 },
    ],
  };
}

export function downloadJson(filename: string, data: unknown) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function downloadUrl(filename: string, url: string) {
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
}

export function speakNarration(text: string) {
  if (typeof window === "undefined" || !("speechSynthesis" in window)) return;
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text.slice(0, 400));
  u.rate = 0.92;
  u.pitch = 0.9;
  window.speechSynthesis.speak(u);
}
