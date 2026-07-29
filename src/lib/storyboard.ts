import type { Genre, Scene, Shot, Tone } from "@/lib/types";
import { STUDIO } from "@/lib/types";

const PALETTES: Record<
  Genre,
  { sky: string; mid: string; ground: string; accent: string; fog: string }
> = {
  noir: {
    sky: "#0b0d12",
    mid: "#1a2030",
    ground: "#0a0a0c",
    accent: "#c9b896",
    fog: "rgba(180,190,210,0.12)",
  },
  scifi: {
    sky: "#070b14",
    mid: "#101a2c",
    ground: "#05070d",
    accent: "#8ec5d8",
    fog: "rgba(100,180,220,0.1)",
  },
  drama: {
    sky: "#14110f",
    mid: "#2a221c",
    ground: "#0e0c0a",
    accent: "#d4b48c",
    fog: "rgba(220,190,150,0.08)",
  },
  thriller: {
    sky: "#0c1014",
    mid: "#182028",
    ground: "#080a0c",
    accent: "#b8c0c8",
    fog: "rgba(160,180,200,0.1)",
  },
  fantasy: {
    sky: "#0c0e18",
    mid: "#1a1830",
    ground: "#09080f",
    accent: "#c4b8d8",
    fog: "rgba(180,160,220,0.1)",
  },
  western: {
    sky: "#1a140c",
    mid: "#3a2a18",
    ground: "#120e08",
    accent: "#d2b48c",
    fog: "rgba(210,180,120,0.1)",
  },
  romance: {
    sky: "#120f14",
    mid: "#2a1e24",
    ground: "#0c0a0c",
    accent: "#e0c4c0",
    fog: "rgba(230,190,190,0.1)",
  },
  horror: {
    sky: "#0a0c0a",
    mid: "#141816",
    ground: "#060706",
    accent: "#9aaa9a",
    fog: "rgba(140,160,140,0.12)",
  },
};

function mulberry32(a: number) {
  return function () {
    let t = (a += 0x6d2b79f5);
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function hashStr(s: string) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

function drawGrain(ctx: CanvasRenderingContext2D, w: number, h: number, rng: () => number) {
  const image = ctx.createImageData(w, h);
  const data = image.data;
  for (let i = 0; i < data.length; i += 4) {
    const v = 20 + rng() * 40;
    data[i] = v;
    data[i + 1] = v;
    data[i + 2] = v;
    data[i + 3] = rng() > 0.7 ? 28 : 0;
  }
  ctx.putImageData(image, 0, 0);
}

function drawSkyline(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  color: string,
  rng: () => number,
) {
  ctx.fillStyle = color;
  const baseY = h * 0.55;
  let x = 0;
  while (x < w) {
    const bw = 18 + rng() * 50;
    const bh = 40 + rng() * (h * 0.35);
    ctx.fillRect(x, baseY - bh, bw, bh + h * 0.2);
    ctx.fillStyle = `rgba(220,200,160,${0.05 + rng() * 0.2})`;
    for (let wy = baseY - bh + 8; wy < baseY - 10; wy += 12) {
      for (let wx = x + 4; wx < x + bw - 4; wx += 8) {
        if (rng() > 0.55) ctx.fillRect(wx, wy, 3, 4);
      }
    }
    ctx.fillStyle = color;
    x += bw + 2;
  }
}

function drawFigure(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  scale: number,
  color: string,
) {
  ctx.save();
  ctx.translate(x, y);
  ctx.scale(scale, scale);
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.ellipse(0, -42, 10, 12, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.beginPath();
  ctx.moveTo(-14, -28);
  ctx.lineTo(14, -28);
  ctx.lineTo(18, 20);
  ctx.lineTo(-18, 20);
  ctx.closePath();
  ctx.fill();
  ctx.fillRect(-16, 20, 10, 28);
  ctx.fillRect(6, 20, 10, 28);
  ctx.restore();
}

function drawInterior(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  palette: (typeof PALETTES)[Genre],
  rng: () => number,
) {
  const g = ctx.createLinearGradient(0, 0, 0, h);
  g.addColorStop(0, palette.mid);
  g.addColorStop(1, palette.ground);
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, w, h);

  const wx = w * (0.55 + rng() * 0.2);
  const wy = h * 0.18;
  const ww = w * 0.22;
  const wh = h * 0.35;
  const light = ctx.createLinearGradient(wx, wy, wx, wy + wh * 2);
  light.addColorStop(0, palette.accent + "55");
  light.addColorStop(1, "transparent");
  ctx.fillStyle = light;
  ctx.beginPath();
  ctx.moveTo(wx, wy);
  ctx.lineTo(wx + ww, wy);
  ctx.lineTo(wx + ww + 40, h);
  ctx.lineTo(wx - 80, h);
  ctx.closePath();
  ctx.fill();

  ctx.strokeStyle = palette.accent + "40";
  ctx.lineWidth = 2;
  ctx.strokeRect(wx, wy, ww, wh);
  ctx.beginPath();
  ctx.moveTo(wx + ww / 2, wy);
  ctx.lineTo(wx + ww / 2, wy + wh);
  ctx.moveTo(wx, wy + wh / 2);
  ctx.lineTo(wx + ww, wy + wh / 2);
  ctx.stroke();
}

export function paintFrame(opts: {
  width?: number;
  height?: number;
  genre: Genre;
  tone: Tone;
  scene: Scene;
  shot: Shot;
  title?: string;
}): string {
  const width = opts.width ?? 960;
  const height = opts.height ?? 540;
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d")!;
  const palette = PALETTES[opts.genre];
  const seed = hashStr(opts.scene.id + opts.shot.id + opts.shot.description);
  const rng = mulberry32(seed);

  if (opts.scene.interior) {
    drawInterior(ctx, width, height, palette, rng);
  } else {
    const sky = ctx.createLinearGradient(0, 0, 0, height);
    sky.addColorStop(0, palette.sky);
    sky.addColorStop(0.55, palette.mid);
    sky.addColorStop(1, palette.ground);
    ctx.fillStyle = sky;
    ctx.fillRect(0, 0, width, height);
    drawSkyline(ctx, width, height, palette.ground, rng);
    ctx.fillStyle = palette.fog;
    ctx.fillRect(0, height * 0.45, width, height * 0.25);
  }

  if (opts.scene.timeOfDay === "NIGHT" || opts.scene.timeOfDay === "DUSK") {
    ctx.beginPath();
    ctx.fillStyle = palette.accent + "99";
    ctx.arc(width * 0.78, height * 0.18, 18 + rng() * 10, 0, Math.PI * 2);
    ctx.fill();
  }

  const figColor = "rgba(8,8,10,0.85)";
  if (opts.shot.type === "closeup") {
    drawFigure(ctx, width * 0.5, height * 0.78, 2.2 + rng() * 0.3, figColor);
  } else if (opts.shot.type === "medium") {
    drawFigure(ctx, width * 0.42, height * 0.72, 1.4, figColor);
    if (rng() > 0.4) drawFigure(ctx, width * 0.58, height * 0.72, 1.35, figColor);
  } else if (opts.shot.type !== "title" && opts.shot.type !== "actcard") {
    drawFigure(ctx, width * (0.3 + rng() * 0.4), height * 0.7, 0.9 + rng() * 0.4, figColor);
  }

  ctx.fillStyle = "#000";
  ctx.fillRect(0, 0, width, height * 0.08);
  ctx.fillRect(0, height * 0.92, width, height * 0.08);

  const vig = ctx.createRadialGradient(
    width / 2,
    height / 2,
    height * 0.2,
    width / 2,
    height / 2,
    height * 0.75,
  );
  vig.addColorStop(0, "transparent");
  vig.addColorStop(1, "rgba(0,0,0,0.55)");
  ctx.fillStyle = vig;
  ctx.fillRect(0, 0, width, height);

  if (opts.shot.type === "title" && opts.title) {
    ctx.fillStyle = "rgba(0,0,0,0.45)";
    ctx.fillRect(0, 0, width, height);
    ctx.fillStyle = palette.accent;
    ctx.font = `500 ${Math.floor(width * 0.055)}px "Cormorant Garamond", Georgia, serif`;
    ctx.textAlign = "center";
    ctx.fillText(opts.title.toUpperCase(), width / 2, height * 0.48);
    ctx.font = `400 ${Math.floor(width * 0.018)}px "DM Sans", system-ui, sans-serif`;
    ctx.fillStyle = "rgba(230,225,215,0.7)";
    ctx.fillText(STUDIO.credit.toUpperCase(), width / 2, height * 0.56);
  }

  if (opts.shot.type === "actcard") {
    ctx.fillStyle = "rgba(0,0,0,0.5)";
    ctx.fillRect(0, 0, width, height);
    ctx.fillStyle = palette.accent;
    ctx.font = `500 ${Math.floor(width * 0.04)}px "Cormorant Garamond", Georgia, serif`;
    ctx.textAlign = "center";
    ctx.fillText(`ACT ${opts.scene.act}`, width / 2, height * 0.48);
    ctx.font = `400 ${Math.floor(width * 0.018)}px "DM Sans", system-ui, sans-serif`;
    ctx.fillStyle = "rgba(230,225,215,0.7)";
    ctx.fillText("TGRM · FRACTURE GRADIENT", width / 2, height * 0.56);
  }

  ctx.fillStyle = "rgba(0,0,0,0.5)";
  ctx.fillRect(16, height - 48, width - 32, 28);
  ctx.fillStyle = "rgba(230,225,215,0.85)";
  ctx.font = `500 11px "DM Sans", system-ui, sans-serif`;
  ctx.textAlign = "left";
  ctx.fillText(
    `${opts.scene.slugline}  ·  ${opts.shot.type.toUpperCase()}  ·  A${opts.scene.act}/CH${opts.scene.chapter}`,
    28,
    height - 30,
  );

  ctx.globalAlpha = 0.15;
  drawGrain(ctx, width, height, rng);
  ctx.globalAlpha = 1;

  return canvas.toDataURL("image/jpeg", 0.86);
}

export function boardScenes(
  scenes: Scene[],
  genre: Genre,
  tone: Tone,
  title: string,
): Scene[] {
  return scenes.map((scene) => ({
    ...scene,
    shots: scene.shots.map((shot) => ({
      ...shot,
      frameDataUrl: paintFrame({ genre, tone, scene, shot, title }),
    })),
  }));
}

export function paintPoster(opts: {
  title: string;
  genre: Genre;
  logline: string;
}): string {
  const width = 640;
  const height = 960;
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d")!;
  const palette = PALETTES[opts.genre];
  const rng = mulberry32(hashStr(opts.title + opts.genre));

  const g = ctx.createLinearGradient(0, 0, 0, height);
  g.addColorStop(0, palette.sky);
  g.addColorStop(0.5, palette.mid);
  g.addColorStop(1, palette.ground);
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, width, height);
  drawSkyline(ctx, width, height, palette.ground, rng);
  drawFigure(ctx, width * 0.5, height * 0.62, 2.4, "rgba(6,6,8,0.9)");

  ctx.fillStyle = "rgba(0,0,0,0.35)";
  ctx.fillRect(0, height * 0.62, width, height * 0.38);

  ctx.fillStyle = palette.accent;
  ctx.font = `500 42px "Cormorant Garamond", Georgia, serif`;
  ctx.textAlign = "center";
  const words = opts.title.toUpperCase().split(" ");
  let line = "";
  let y = height * 0.72;
  for (const word of words) {
    const test = line ? `${line} ${word}` : word;
    if (ctx.measureText(test).width > width - 80 && line) {
      ctx.fillText(line, width / 2, y);
      line = word;
      y += 48;
    } else {
      line = test;
    }
  }
  if (line) ctx.fillText(line, width / 2, y);

  ctx.fillStyle = "rgba(230,225,215,0.65)";
  ctx.font = `400 14px "DM Sans", system-ui, sans-serif`;
  const log = opts.logline.slice(0, 110) + (opts.logline.length > 110 ? "…" : "");
  wrapText(ctx, log, width / 2, y + 40, width - 100, 20);

  ctx.font = `500 11px "DM Sans", system-ui, sans-serif`;
  ctx.fillStyle = palette.accent + "cc";
  ctx.fillText("REPARODYNAMICS · TGRM · SILVER-SCREEN", width / 2, height - 40);

  return canvas.toDataURL("image/jpeg", 0.9);
}

function wrapText(
  ctx: CanvasRenderingContext2D,
  text: string,
  x: number,
  y: number,
  maxWidth: number,
  lineHeight: number,
) {
  const words = text.split(" ");
  let line = "";
  let yy = y;
  for (const w of words) {
    const test = line ? `${line} ${w}` : w;
    if (ctx.measureText(test).width > maxWidth && line) {
      ctx.fillText(line, x, yy);
      line = w;
      yy += lineHeight;
    } else {
      line = test;
    }
  }
  if (line) ctx.fillText(line, x, yy);
}
