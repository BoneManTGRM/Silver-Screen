import type { MsilReport, RyeMetrics, ScarMemory, TgrmCycleLog } from "@/lib/reparodynamics/science";

export type Genre =
  | "noir"
  | "scifi"
  | "drama"
  | "thriller"
  | "fantasy"
  | "western"
  | "romance"
  | "horror";

export type Tone =
  | "cinematic"
  | "intimate"
  | "epic"
  | "melancholy"
  | "tense"
  | "hopeful";

/** Runtime target for Reparodynamics · TGRM production */
export type FilmFormat = "trailer" | "short" | "episode" | "featurette" | "feature";

export type FilmStage =
  | "brief"
  | "script"
  | "cast"
  | "storyboard"
  | "timeline"
  | "render"
  | "nft";

export type ShotType =
  | "establishing"
  | "wide"
  | "medium"
  | "closeup"
  | "insert"
  | "pov"
  | "title"
  | "actcard";

export interface Character {
  id: string;
  name: string;
  role: string;
  description: string;
  arc: string;
}

export interface Shot {
  id: string;
  type: ShotType;
  description: string;
  dialogue?: string;
  durationSec: number;
  frameDataUrl?: string;
}

export interface Scene {
  id: string;
  number: number;
  act: number;
  chapter: number;
  slugline: string;
  summary: string;
  location: string;
  timeOfDay: "DAY" | "NIGHT" | "DAWN" | "DUSK";
  interior: boolean;
  shots: Shot[];
  durationSec: number;
  /** Script-page estimate contribution (1 page ≈ 1 min) */
  scriptMinutes: number;
}

export interface Act {
  number: number;
  title: string;
  purpose: string;
  sceneIds: string[];
}

export interface Chapter {
  number: number;
  act: number;
  title: string;
  sceneIds: string[];
  targetMinutes: number;
  renderUrl?: string;
}

export interface FilmProject {
  id: string;
  title: string;
  premise: string;
  genre: Genre;
  tone: Tone;
  format: FilmFormat;
  targetMinutes: number;
  logline: string;
  script: string;
  characters: Character[];
  scenes: Scene[];
  acts: Act[];
  chapters: Chapter[];
  createdAt: string;
  updatedAt: string;
  status: "draft" | "scripted" | "boarded" | "rendered" | "packaged";
  renderUrl?: string;
  posterDataUrl?: string;
  pipelineProgress: number;
  pipelineLabel: string;
  nftMetadata?: NftMetadata;
  studio: "Reparodynamics";
  pipeline: "TGRM";
  /** TGRM / RYE / MSIL telemetry */
  rye?: RyeMetrics;
  msil?: MsilReport;
  tgrmLog?: TgrmCycleLog[];
  scars?: ScarMemory[];
}

export interface NftMetadata {
  name: string;
  description: string;
  attributes: { trait_type: string; value: string | number }[];
  external_url: string;
  animation_url?: string;
  image?: string;
  collection?: string;
  studio?: string;
}

export const STUDIO = {
  name: "Reparodynamics",
  pipeline: "TGRM",
  pipelineFull: "Targeted Gradient Repair Mechanism",
  rye: "Repair Yield per Energy",
  msil: "Meta Stability Intelligence Layer",
  tagline:
    "Self-repairing narrative systems — detect fractures, minimal correction, verify, reinforce.",
  credit: "A Reparodynamics Production · TGRM · RYE · MSIL",
  github: "https://github.com/BoneManTGRM/Silver-Screen",
  x: "https://x.com/Reparodynamics",
  founder: "Cody Ryan Jenkins",
} as const;

export const FORMATS: {
  id: FilmFormat;
  label: string;
  minutes: number;
  scenes: number;
  acts: number;
  chapters: number;
  hint: string;
}[] = [
  { id: "trailer", label: "Trailer", minutes: 2, scenes: 5, acts: 1, chapters: 1, hint: "2-min sizzle" },
  { id: "short", label: "Short", minutes: 12, scenes: 8, acts: 2, chapters: 2, hint: "12-min short" },
  { id: "episode", label: "Episode", minutes: 24, scenes: 12, acts: 3, chapters: 3, hint: "24-min episode" },
  { id: "featurette", label: "Featurette", minutes: 45, scenes: 16, acts: 3, chapters: 4, hint: "45-min mid-form" },
  { id: "feature", label: "Feature", minutes: 90, scenes: 24, acts: 3, chapters: 8, hint: "90-min full film" },
];

export const GENRES: { id: Genre; label: string; hint: string }[] = [
  { id: "noir", label: "Noir", hint: "Rain, shadows, moral gray" },
  { id: "scifi", label: "Sci-Fi", hint: "Future cities, tech dread" },
  { id: "drama", label: "Drama", hint: "Human stakes, quiet power" },
  { id: "thriller", label: "Thriller", hint: "Clock, chase, reveal" },
  { id: "fantasy", label: "Fantasy", hint: "Myth, wonder, quest" },
  { id: "western", label: "Western", hint: "Dust, justice, horizon" },
  { id: "romance", label: "Romance", hint: "Longing, near-miss, light" },
  { id: "horror", label: "Horror", hint: "Dread, silence, breach" },
];

export const TONES: { id: Tone; label: string }[] = [
  { id: "cinematic", label: "Cinematic" },
  { id: "intimate", label: "Intimate" },
  { id: "epic", label: "Epic" },
  { id: "melancholy", label: "Melancholy" },
  { id: "tense", label: "Tense" },
  { id: "hopeful", label: "Hopeful" },
];

export const STAGES: { id: FilmStage; label: string; step: number }[] = [
  { id: "brief", label: "Brief", step: 1 },
  { id: "script", label: "Script", step: 2 },
  { id: "cast", label: "Cast", step: 3 },
  { id: "storyboard", label: "Board", step: 4 },
  { id: "timeline", label: "Cut", step: 5 },
  { id: "render", label: "Render", step: 6 },
  { id: "nft", label: "NFT", step: 7 },
];

export function formatMeta(format: FilmFormat) {
  return FORMATS.find((f) => f.id === format) ?? FORMATS[0]!;
}
