import { uid } from "@/lib/utils";
import { runTgrm } from "@/lib/reparodynamics/tgrm";
import type { ScarMemory } from "@/lib/reparodynamics/science";
import {
  formatMeta,
  type Act,
  type Chapter,
  type Character,
  type FilmFormat,
  type FilmProject,
  type Genre,
  type Scene,
  type Shot,
  type Tone,
} from "@/lib/types";

const FIRST_NAMES = [
  "Elena", "Marcus", "Sofia", "Kai", "Vera", "Diego", "Ava", "Rafael",
  "Luna", "Orion", "Iris", "Caleb", "Nia", "Jonas", "Mira", "Noah",
  "Seraph", "Quinn", "Imani", "Theo",
];

const SURNAMES = [
  "Cross", "Vale", "Reyes", "Kade", "Morrow", "Solis", "Hart", "Quill",
  "Voss", "Navarro", "Ash", "Lane", "Cortez", "Frost", "Blackwood", "Sato",
  "Okoye", "Mercer", "Duarte", "Shin",
];

const LOCATIONS: Record<Genre, string[]> = {
  noir: ["RAIN-SOAKED ALLEY", "SMOKY BAR", "DETECTIVE'S OFFICE", "ROOFTOP", "POLICE ARCHIVE", "RIVER DOCKS", "COURT ANNEX"],
  scifi: ["NEON CORRIDOR", "ORBITAL DOCK", "DATA VAULT", "WASTELAND OUTPOST", "BRIDGE DECK", "GENE LAB", "NULL MARKET"],
  drama: ["FAMILY KITCHEN", "HOSPITAL HALL", "EMPTY THEATER", "CITY BUS", "COURTYARD", "SCHOOL GYM", "APARTMENT STAIR"],
  thriller: ["SAFEHOUSE", "SUBWAY PLATFORM", "SERVER ROOM", "HIGHWAY OVERPASS", "HOTEL SUITE", "PARKING GARAGE", "BORDER CHECK"],
  fantasy: ["ANCIENT LIBRARY", "CLIFF TEMPLE", "MARKET OF SHADOWS", "MOONLIT FOREST", "THRONE HALL", "SALT MINE", "MIRROR LAKE"],
  western: ["DUSTY SALOON", "OPEN PRAIRIE", "SHERIFF'S OFFICE", "RAILROAD DEPOT", "CANYON PASS", "CLAIM SITE", "CHURCH STEPS"],
  romance: ["BOOKSHOP", "RAINY CAFE", "FERRY DECK", "BALCONY AT DUSK", "TRAIN COMPARTMENT", "GALLERY OPENING", "NIGHT MARKET"],
  horror: ["ABANDONED WING", "FOGGY CEMETERY", "BASEMENT CORRIDOR", "SEALED ATTIC", "LAKESHORE", "SERVICE TUNNEL", "OLD CHAPEL"],
};

const ROLE_SETS: Record<Genre, string[]> = {
  noir: ["Hard-boiled detective", "Femme fatale client", "Corrupt lieutenant", "Night clerk witness", "Debt collector"],
  scifi: ["Rogue pilot", "AI companion", "Syndicate fixer", "Archivist of the Ring", "Bio-smuggler"],
  drama: ["Estranged parent", "Quiet caregiver", "Ambitious sibling", "Old friend returned", "Neighbor who hears everything"],
  thriller: ["Whistleblower", "Shadow agent", "Handler", "Journalist ally", "Double asset"],
  fantasy: ["Reluctant heir", "Wandering seer", "Fallen knight", "Market thief", "Oath-bound spirit"],
  western: ["Drifter", "Town doctor", "Rail baron", "Widow rancher", "Young deputy"],
  romance: ["Returning artist", "Night-shift baker", "Old flame", "Ferry captain", "Sister who knows"],
  horror: ["Skeptical researcher", "Local guide", "The presence", "Archivist of seals", "Last survivor of the first night"],
};

const TONE_LINES: Record<Tone, string[]> = {
  cinematic: [
    "The frame holds longer than comfort allows.",
    "Light cuts the room like a blade.",
    "Silence becomes the loudest score.",
  ],
  intimate: [
    "They speak in almost-whispers.",
    "A hand almost reaches. Almost.",
    "The room shrinks to two breaths.",
  ],
  epic: [
    "The sky answers with thunder.",
    "History turns on this threshold.",
    "Every step echoes for generations.",
  ],
  melancholy: [
    "Rain keeps the appointments they broke.",
    "Memory arrives late and overdressed.",
    "The past wears yesterday's coat.",
  ],
  tense: [
    "A clock ticks under the floorboards.",
    "Every exit is a rumor.",
    "They smile with their teeth only.",
  ],
  hopeful: [
    "Morning finds a crack in the curtain.",
    "Someone chooses kindness anyway.",
    "The map redraws itself toward home.",
  ],
};

const ACT_TITLES = [
  { title: "Fracture", purpose: "Establish world, wound, and unstable equilibrium." },
  { title: "Gradient", purpose: "Escalate pressure; minimal choices compound into fate." },
  { title: "Repair or Ruin", purpose: "Verify who they become; scar memory seals the ending." },
];

function pick<T>(arr: T[], seed: number): T {
  return arr[Math.abs(seed) % arr.length]!;
}

function hash(str: string): number {
  let h = 0;
  for (let i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) | 0;
  return Math.abs(h);
}

function titleCase(s: string) {
  return s
    .split(/\s+/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(" ");
}

function extractPlace(premise: string): string | null {
  const m = premise.match(/\bin\s+([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){0,2})/);
  if (m?.[1]) return m[1];
  const lower = premise.toLowerCase();
  if (lower.includes("mexico city")) return "Mexico City";
  if (lower.includes("tokyo")) return "Tokyo";
  if (lower.includes("neo")) return "Neo-City";
  return null;
}

export function inventTitle(premise: string, genre: Genre): string {
  const h = hash(premise + genre);
  const place = extractPlace(premise);
  const cores: Record<Genre, string[]> = {
    noir: ["Silver Rain", "Last Alibi", "Night Receipt", "Ash Signal"],
    scifi: ["Quiet Orbit", "Static Horizon", "Null Protocol", "Glass Comet"],
    drama: ["Soft Exit", "Unsaid Hours", "Borrowed Light", "Paper Rooms"],
    thriller: ["Red Margin", "Dead Switch", "Second Key", "Cold Ledger"],
    fantasy: ["Moon Ledger", "Salt Crown", "Ash Prophecy", "Veilward"],
    western: ["Iron Dust", "Last Train Out", "Dry Justice", "Canyon Coin"],
    romance: ["Near Miss", "Dusk Ticket", "Afterglow Map", "Second Glance"],
    horror: ["Below Floor", "The Listening", "Pale Threshold", "House Remembers"],
  };
  const base = pick(cores[genre], h);
  if (place && h % 2 === 0) return `${base}: ${place}`;
  return base;
}

export function inventLogline(
  premise: string,
  genre: Genre,
  tone: Tone,
  characters: Character[],
): string {
  const lead = characters[0]?.name ?? "A stranger";
  const foil = characters[1]?.name ?? "a ghost of the past";
  const place = extractPlace(premise) ?? "a city that never sleeps";
  return `${lead} must confront ${foil} in ${place} before ${
    tone === "hopeful" ? "dawn rewrites the rules" : "the night claims its due"
  } — ${premise.trim().replace(/\.$/, "")}.`;
}

export function generateCharacters(premise: string, genre: Genre, format: FilmFormat): Character[] {
  const h = hash(premise + genre);
  const roles = ROLE_SETS[genre];
  const count = format === "trailer" || format === "short" ? 3 : format === "feature" ? 5 : 4;
  return roles.slice(0, count).map((role, i) => {
    const first = pick(FIRST_NAMES, h + i * 17);
    const last = pick(SURNAMES, h + i * 41 + 3);
    const name = i === 2 && genre === "horror" ? "It" : `${first} ${last}`;
    const arcs = [
      "From denial to resolve",
      "From lure to truth",
      "From control to collapse",
      "From witness to catalyst",
      "From scar to shield",
    ];
    return {
      id: uid("char"),
      name,
      role,
      description: `${role} drawn into: ${premise.slice(0, 90)}${premise.length > 90 ? "…" : ""}`,
      arc: arcs[i] ?? "From fracture to repair",
    };
  });
}

function makeShot(
  type: Shot["type"],
  description: string,
  dialogue: string | undefined,
  durationSec: number,
): Shot {
  return { id: uid("shot"), type, description, dialogue, durationSec };
}

type Beat = { summary: string; focus: string; act: number };

function buildBeats(
  premise: string,
  format: FilmFormat,
  characters: Character[],
): Beat[] {
  const meta = formatMeta(format);
  const n = meta.scenes;
  const lead = characters[0]!;
  const foil = characters[1]!;
  const antag = characters[2] ?? characters[1]!;
  const support = characters[3];
  const acts = meta.acts;

  const core: Beat[] = [
    {
      act: 1,
      focus: "setup",
      summary: `${lead.name} lives inside the unstable equilibrium of: ${premise.slice(0, 100)}.`,
    },
    {
      act: 1,
      focus: "inciting",
      summary: `${foil.name} offers a deal that smells like destiny and danger.`,
    },
    {
      act: 1,
      focus: "debate",
      summary: `${lead.name} resists the call; the world applies pressure.`,
    },
    {
      act: 2,
      focus: "fun_and_games",
      summary: `Investigation expands. ${lead.name} and ${foil.name} map the fracture field.`,
    },
    {
      act: 2,
      focus: "midpoint",
      summary: `A verified truth rewrites the map. ${premise.split(" ").slice(0, 8).join(" ")} is not what it seemed.`,
    },
    {
      act: 2,
      focus: "bad_guys",
      summary: `${antag.name} closes distance. Trust fractures. Energy cost rises.`,
    },
    {
      act: 2,
      focus: "all_is_lost",
      summary: `Minimal corrections fail. ${lead.name} faces the scar they denied.`,
    },
    {
      act: 3,
      focus: "dark_night",
      summary: `Silence. ${support?.name ?? lead.name} holds a final piece of evidence.`,
    },
    {
      act: 3,
      focus: "climax",
      summary: `Final confrontation. ${lead.name} chooses repair or ruin.`,
    },
    {
      act: 3,
      focus: "resolution",
      summary: `New equilibrium. Scar memory remains. The city remembers.`,
    },
  ];

  const beats: Beat[] = [];
  for (let i = 0; i < n; i++) {
    const template = core[Math.min(i, core.length - 1)]!;
    const actNum = Math.min(acts, Math.floor((i / n) * acts) + 1);
    beats.push({
      act: actNum,
      focus: template.focus,
      summary:
        i === 0
          ? template.summary
          : i === n - 1
            ? `${lead.name} seals the outcome of: ${premise.slice(0, 80)}.`
            : `${template.summary} [beat ${i + 1}/${n}]`,
    });
  }
  return beats;
}

export function generateScenes(
  premise: string,
  genre: Genre,
  tone: Tone,
  characters: Character[],
  format: FilmFormat,
): { scenes: Scene[]; acts: Act[]; chapters: Chapter[] } {
  const h = hash(premise + genre + tone + format);
  const meta = formatMeta(format);
  const locs = LOCATIONS[genre];
  const lead = characters[0]!;
  const foil = characters[1]!;
  const antag = characters[2] ?? foil;
  const place = extractPlace(premise);
  const times: Scene["timeOfDay"][] = ["NIGHT", "DAY", "DUSK", "DAWN", "NIGHT", "DAY"];
  const beats = buildBeats(premise, format, characters);
  const minutesPerScene = meta.minutes / Math.max(1, beats.length);

  const scenes: Scene[] = beats.map((beat, i) => {
    const loc = i === 0 && place ? place.toUpperCase() : pick(locs, h + i * 9);
    const time = times[i % times.length]!;
    const interior = i % 2 === 0;
    const slugline = `${interior ? "INT" : "EXT"}. ${loc} — ${time}`;
    const toneLine = pick(TONE_LINES[tone], h + i);
    const speaker = i % 3 === 0 ? lead.name : i % 3 === 1 ? foil.name : antag.name;
    const dialogueA = `${speaker}: ${
      i === 0
        ? "I didn't ask for this story. It found me."
        : i === beats.length - 1
          ? "Then we end it. On our terms."
          : toneLine
    }`;

    const isTitle = i === 0;
    const isActOpen = i === 0 || beats[i - 1]?.act !== beat.act;
    const shots: Shot[] = [];

    if (isTitle) {
      shots.push(makeShot("title", `Title card over ${loc.toLowerCase()} atmosphere.`, undefined, 3.2));
    } else if (isActOpen) {
      shots.push(
        makeShot(
          "actcard",
          `Act ${beat.act} card — ${ACT_TITLES[beat.act - 1]?.title ?? "Movement"}.`,
          undefined,
          2.4,
        ),
      );
    } else {
      shots.push(
        makeShot("establishing", `Wide establish of ${loc.toLowerCase()}. ${toneLine}`, undefined, 2.6),
      );
    }

    shots.push(
      makeShot(
        "medium",
        `${lead.name} in frame. Wardrobe and posture telegraph ${genre}.`,
        dialogueA,
        3.8,
      ),
      makeShot(
        i === beats.length - 1 ? "closeup" : "wide",
        beat.summary,
        i % 2 === 1 ? `${foil.name}: ${toneLine}` : undefined,
        3.2,
      ),
      makeShot(
        "closeup",
        `Emotional beat — eyes, hands, a single decisive detail. Reparodynamic scar visible.`,
        i === beats.length - 1
          ? `${lead.name}: ${titleCase(premise.split(" ").slice(0, 5).join(" "))}… ends here.`
          : undefined,
        2.6,
      ),
    );

    if (format === "feature" || format === "featurette") {
      shots.push(
        makeShot(
          "insert",
          `Insert detail that foreshadows act ${Math.min(meta.acts, beat.act + 1)}.`,
          undefined,
          2.0,
        ),
      );
    }

    const durationSec = shots.reduce((s, sh) => s + sh.durationSec, 0);
    const chapter = Math.min(meta.chapters, Math.floor((i / beats.length) * meta.chapters) + 1);

    return {
      id: uid("scene"),
      number: i + 1,
      act: beat.act,
      chapter,
      slugline,
      summary: beat.summary,
      location: loc,
      timeOfDay: time,
      interior,
      shots,
      durationSec,
      scriptMinutes: +minutesPerScene.toFixed(2),
    };
  });

  const acts: Act[] = Array.from({ length: meta.acts }, (_, i) => {
    const number = i + 1;
    const metaAct = ACT_TITLES[i] ?? { title: `Act ${number}`, purpose: "Advance the fracture field." };
    return {
      number,
      title: metaAct.title,
      purpose: metaAct.purpose,
      sceneIds: scenes.filter((s) => s.act === number).map((s) => s.id),
    };
  });

  const chapters: Chapter[] = Array.from({ length: meta.chapters }, (_, i) => {
    const number = i + 1;
    const chapterScenes = scenes.filter((s) => s.chapter === number);
    const act = chapterScenes[0]?.act ?? 1;
    return {
      number,
      act,
      title: `Chapter ${number}`,
      sceneIds: chapterScenes.map((s) => s.id),
      targetMinutes: +(meta.minutes / meta.chapters).toFixed(2),
    };
  });

  return { scenes, acts, chapters };
}

export function formatScreenplay(
  title: string,
  logline: string,
  characters: Character[],
  scenes: Scene[],
  targetMinutes: number,
): string {
  const cast = characters
    .map((c) => `  ${c.name.toUpperCase()} — ${c.role}. ${c.arc}.`)
    .join("\n");

  const body = scenes
    .map((scene) => {
      const shots = scene.shots
        .map((sh) => {
          const head = `  [${sh.type.toUpperCase()}] ${sh.description}`;
          return sh.dialogue ? `${head}\n\n                    ${sh.dialogue}` : head;
        })
        .join("\n\n");
      return `ACT ${scene.act} · CH ${scene.chapter}\n${scene.slugline}\n\n${scene.summary}\n\n${shots}`;
    })
    .join("\n\n\n");

  return (
    `${title.toUpperCase()}\n\n` +
    `REPARODYNAMICS · TGRM SCREENPLAY\n` +
    `LOGLINE\n${logline}\n\n` +
    `CAST\n${cast}\n\n` +
    `TARGET RUNTIME: ${targetMinutes} min\n\n` +
    `FADE IN:\n\n${body}\n\nFADE OUT.\n\nTHE END\n`
  );
}

export function buildFilmFromBrief(input: {
  premise: string;
  genre: Genre;
  tone: Tone;
  title?: string;
  format?: FilmFormat;
  scars?: ScarMemory[];
}): Pick<
  FilmProject,
  | "title"
  | "logline"
  | "script"
  | "characters"
  | "scenes"
  | "acts"
  | "chapters"
  | "status"
  | "format"
  | "targetMinutes"
  | "rye"
  | "msil"
  | "tgrmLog"
  | "scars"
  | "studio"
  | "pipeline"
> {
  const premise =
    input.premise.trim() || "A stranger inherits a secret that rewrites the city.";
  const format = input.format ?? "short";
  const meta = formatMeta(format);
  const characters = generateCharacters(premise, input.genre, format);
  const title = input.title?.trim() || inventTitle(premise, input.genre);
  const { scenes, acts, chapters } = generateScenes(
    premise,
    input.genre,
    input.tone,
    characters,
    format,
  );
  const logline = inventLogline(premise, input.genre, input.tone, characters);
  let script = formatScreenplay(title, logline, characters, scenes, meta.minutes);

  const tgrm = runTgrm({
    title,
    premise,
    logline,
    script,
    characters,
    scenes,
    acts,
    chapters,
    targetMinutes: meta.minutes,
    scars: input.scars ?? [],
  });

  return {
    title,
    logline: tgrm.state.logline,
    script: tgrm.state.script,
    characters: tgrm.state.characters,
    scenes: tgrm.state.scenes,
    acts: tgrm.state.acts,
    chapters: tgrm.state.chapters,
    status: "scripted",
    format,
    targetMinutes: meta.minutes,
    rye: tgrm.metrics,
    msil: tgrm.msil,
    tgrmLog: tgrm.log,
    scars: tgrm.scars,
    studio: "Reparodynamics",
    pipeline: "TGRM",
  };
}
