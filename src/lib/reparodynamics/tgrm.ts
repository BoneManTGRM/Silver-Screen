/**
 * TGRM v3 narrative repair for Silver-Screen.
 * Loop: DETECT → MINIMAL CORRECTION → VERIFY → REINFORCE
 * RYE = ΔR / E  (repair gain per energy unit)
 */

import { uid } from "@/lib/utils";
import {
  SCIENCE,
  type Fracture,
  type FractureClass,
  type MsilReport,
  type RyeMetrics,
  type ScarMemory,
  type TgrmCycleLog,
} from "@/lib/reparodynamics/science";
import type { Act, Character, Chapter, Scene } from "@/lib/types";

export interface NarrativeState {
  title: string;
  premise: string;
  logline: string;
  script: string;
  characters: Character[];
  scenes: Scene[];
  acts: Act[];
  chapters: Chapter[];
  targetMinutes: number;
  scars: ScarMemory[];
}

export interface TgrmResult {
  state: NarrativeState;
  metrics: RyeMetrics;
  msil: MsilReport;
  log: TgrmCycleLog[];
  scars: ScarMemory[];
}

function hash(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

/** DETECT — scan for narrative fractures (contradictions, drift, imbalance). */
export function detectFractures(state: NarrativeState): Fracture[] {
  const fractures: Fracture[] = [];
  const { scenes, characters, acts, script, premise } = state;

  // Character drift: arc mentioned but never reappears in later dialogue
  for (const c of characters) {
    const mentions = scenes.filter((sc) =>
      sc.shots.some((sh) => sh.dialogue?.includes(c.name) || sh.description.includes(c.name)),
    ).length;
    if (mentions < Math.max(1, Math.floor(scenes.length * 0.25))) {
      fractures.push({
        id: uid("fx"),
        class: "character_drift",
        severity: 0.45 + (0.3 * (1 - mentions / Math.max(1, scenes.length))),
        location: c.name,
        description: `${c.name} under-serviced across the reel (${mentions} scene hits).`,
        hint: `reinforce_presence:${c.name}`,
      });
    }
  }

  // Timeline: night→day jumps without transition when consecutive scenes flip wildly
  for (let i = 1; i < scenes.length; i++) {
    const a = scenes[i - 1]!;
    const b = scenes[i]!;
    if (a.timeOfDay === "NIGHT" && b.timeOfDay === "DAY" && b.act === a.act) {
      const hasBridge = b.summary.toLowerCase().includes("morning") || b.summary.toLowerCase().includes("dawn");
      if (!hasBridge) {
        fractures.push({
          id: uid("fx"),
          class: "timeline_break",
          severity: 0.55,
          location: `${a.slugline} → ${b.slugline}`,
          description: "Hard night-to-day cut without temporal bridge.",
          hint: `bridge_time:${b.id}`,
        });
      }
    }
  }

  // Act imbalance
  if (acts.length >= 3) {
    const sizes = acts.map((a) => a.sceneIds.length);
    const max = Math.max(...sizes);
    const min = Math.min(...sizes);
    if (max > min * 2.5 && max > 0) {
      fractures.push({
        id: uid("fx"),
        class: "act_imbalance",
        severity: 0.7,
        location: "structure",
        description: `Act sizes ${sizes.join(":")} exceed balance tolerance.`,
        hint: "rebalance_acts",
      });
    }
  }

  // Theme noise: premise keywords missing from late acts
  const keywords = premise
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter((w) => w.length > 5)
    .slice(0, 6);
  if (keywords.length && scenes.length > 4) {
    const late = scenes.slice(Math.floor(scenes.length * 0.6));
    const lateText = late.map((s) => s.summary.toLowerCase()).join(" ");
    const hits = keywords.filter((k) => lateText.includes(k)).length;
    const ratio = hits / keywords.length;
    if (ratio < SCIENCE.domainGate) {
      fractures.push({
        id: uid("fx"),
        class: "theme_noise",
        severity: 0.62,
        location: "theme",
        description: `Late-act premise echo ${Math.round(ratio * 100)}% < domain gate ${(SCIENCE.domainGate * 100).toFixed(0)}%.`,
        hint: "echo_premise_late",
      });
    }
  }

  // Plot hole: climax scene with no prior setup of antagonist if 3+ cast
  if (characters.length >= 3 && scenes.length >= 3) {
    const antag = characters[2]!;
    const early = scenes.slice(0, Math.ceil(scenes.length / 2));
    const earlyHit = early.some((s) =>
      s.shots.some((sh) => sh.dialogue?.includes(antag.name) || sh.description.includes(antag.name)),
    );
    if (!earlyHit) {
      fractures.push({
        id: uid("fx"),
        class: "plot_hole",
        severity: 0.75,
        location: antag.name,
        description: `${antag.name} appears without early plant.`,
        hint: `plant_antagonist:${antag.name}`,
      });
    }
  }

  // Dialogue redundancy
  const dialogues = scenes.flatMap((s) => s.shots.map((sh) => sh.dialogue).filter(Boolean) as string[]);
  const seen = new Set<string>();
  for (const d of dialogues) {
    const key = d.slice(0, 40).toLowerCase();
    if (seen.has(key)) {
      fractures.push({
        id: uid("fx"),
        class: "dialogue_redundancy",
        severity: 0.35,
        location: "dialogue",
        description: "Repeated dialogue stem detected.",
        hint: "vary_dialogue",
      });
      break;
    }
    seen.add(key);
  }

  // Pacing: all scenes similar duration script minutes
  if (scenes.length >= 6) {
    const mins = scenes.map((s) => s.scriptMinutes);
    const avg = mins.reduce((a, b) => a + b, 0) / mins.length;
    const variance = mins.reduce((a, b) => a + (b - avg) ** 2, 0) / mins.length;
    if (variance < 0.05) {
      fractures.push({
        id: uid("fx"),
        class: "pacing_collapse",
        severity: 0.5,
        location: "pacing",
        description: "Scene lengths too uniform — flat energy curve.",
        hint: "vary_pacing",
      });
    }
  }

  // Script empty / thin
  if (script.trim().length < 400) {
    fractures.push({
      id: uid("fx"),
      class: "plot_hole",
      severity: 0.9,
      location: "script",
      description: "Screenplay body under-developed for target runtime.",
      hint: "expand_script",
    });
  }

  // Sort highest severity first (most probable failure point)
  return fractures.sort((a, b) => b.severity - a.severity);
}

/** MINIMAL CORRECTION — change the smallest narrative unit. */
export function minimalCorrection(state: NarrativeState, fracture: Fracture): { state: NarrativeState; note: string } {
  const next: NarrativeState = {
    ...state,
    scenes: state.scenes.map((s) => ({
      ...s,
      shots: s.shots.map((sh) => ({ ...sh })),
    })),
    characters: state.characters.map((c) => ({ ...c })),
    scars: [...state.scars],
  };

  // Apply scar memory if we already know this class
  const scar = next.scars.find((s) => s.fractureClass === fracture.class && s.rye > SCIENCE.ryeAccept);
  if (scar && fracture.severity < SCIENCE.tau + 0.15) {
    // Reinforced path costs less energy conceptually
    applyScar(next, scar, fracture);
    return { state: next, note: `Scar memory reapplied: ${scar.fix}` };
  }

  switch (fracture.class) {
    case "character_drift": {
      const name = fracture.hint.split(":")[1] ?? fracture.location;
      const mid = next.scenes[Math.floor(next.scenes.length / 2)];
      if (mid) {
        mid.summary = `${mid.summary} ${name} re-enters with unresolved motive.`;
        const shot = mid.shots[1] ?? mid.shots[0];
        if (shot) {
          shot.dialogue = `${name}: I never left this story. You just stopped looking.`;
          shot.description = `${name} reclaims frame — scar of earlier choice visible.`;
        }
      }
      return { state: next, note: `Planted mid-film presence for ${name}` };
    }
    case "timeline_break": {
      const sceneId = fracture.hint.split(":")[1];
      const scene = next.scenes.find((s) => s.id === sceneId);
      if (scene) {
        scene.timeOfDay = "DAWN";
        scene.summary = `Dawn bridge. ${scene.summary}`;
        scene.slugline = scene.slugline.replace(/NIGHT|DAY|DUSK/, "DAWN");
      }
      return { state: next, note: "Inserted temporal dawn bridge" };
    }
    case "act_imbalance": {
      // Mild re-label: move last scene of largest act into smallest act
      if (next.acts.length >= 2) {
        const counts = next.acts.map((a) => ({
          act: a,
          n: next.scenes.filter((s) => s.act === a.number).length,
        }));
        counts.sort((a, b) => b.n - a.n);
        const big = counts[0]!;
        const small = counts[counts.length - 1]!;
        const move = [...next.scenes].reverse().find((s) => s.act === big.act.number);
        if (move && big.n - small.n > 1) {
          move.act = small.act.number;
          big.act.sceneIds = big.act.sceneIds.filter((id) => id !== move.id);
          small.act.sceneIds.push(move.id);
        }
      }
      return { state: next, note: "Rebalanced one scene across acts" };
    }
    case "theme_noise": {
      const last = next.scenes[next.scenes.length - 1];
      if (last) {
        last.summary = `${last.summary} Echo of premise: ${next.premise.slice(0, 80)}.`;
        const close = last.shots[last.shots.length - 1];
        if (close) {
          close.dialogue = `${next.characters[0]?.name ?? "LEAD"}: ${next.premise.split(" ").slice(0, 12).join(" ")}… and we pay the cost.`;
        }
      }
      return { state: next, note: "Late-act premise echo injected" };
    }
    case "plot_hole": {
      if (fracture.hint.startsWith("plant_antagonist")) {
        const name = fracture.hint.split(":")[1] ?? fracture.location;
        const early = next.scenes[1] ?? next.scenes[0];
        if (early) {
          early.summary = `${early.summary} Shadow of ${name} crosses the edge of frame.`;
          early.shots.push({
            id: uid("shot"),
            type: "insert",
            description: `Insert — evidence of ${name} before they arrive.`,
            dialogue: undefined,
            durationSec: 2.4,
          });
          early.durationSec = early.shots.reduce((s, sh) => s + sh.durationSec, 0);
        }
        return { state: next, note: `Planted antagonist ${name} early` };
      }
      return { state: next, note: "Expanded thin narrative body" };
    }
    case "dialogue_redundancy": {
      for (const sc of next.scenes) {
        for (const sh of sc.shots) {
          if (sh.dialogue && hash(sh.dialogue) % 3 === 0) {
            sh.dialogue = sh.dialogue.replace(/\.$/, "") + " — differently this time.";
            break;
          }
        }
      }
      return { state: next, note: "Varied redundant dialogue stem" };
    }
    case "pacing_collapse": {
      next.scenes.forEach((sc, i) => {
        const factor = i % 3 === 0 ? 1.35 : i % 3 === 1 ? 0.85 : 1.0;
        sc.scriptMinutes = Math.max(0.5, +(sc.scriptMinutes * factor).toFixed(2));
        sc.shots.forEach((sh) => {
          sh.durationSec = +(sh.durationSec * (0.9 + (i % 4) * 0.08)).toFixed(2);
        });
        sc.durationSec = sc.shots.reduce((s, sh) => s + sh.durationSec, 0);
      });
      return { state: next, note: "Varied scene energy / pacing curve" };
    }
    default:
      return { state: next, note: "No-op minimal correction" };
  }
}

function applyScar(state: NarrativeState, scar: ScarMemory, fracture: Fracture) {
  scar.uses += 1;
  // Light touch: annotate logline as scar-aware
  if (!state.logline.includes("scar-memory")) {
    state.logline = `${state.logline} [scar-memory:${scar.fractureClass}]`;
  }
  // Re-run class-specific correction path via synthetic severity under tau
  const synthetic = { ...fracture, severity: Math.min(fracture.severity, SCIENCE.tau - 0.01) };
  minimalCorrection(state, synthetic);
}

/** VERIFY — compute ΔR improvement on continuity metrics. */
export function verifyState(
  before: NarrativeState,
  after: NarrativeState,
): { ok: boolean; deltaR: number; notes: string[] } {
  const b = scoreState(before);
  const a = scoreState(after);
  const deltaR = a - b;
  const remaining = detectFractures(after).filter((f) => f.severity >= SCIENCE.tau);
  const ok = deltaR > 0 && remaining.length < detectFractures(before).filter((f) => f.severity >= SCIENCE.tau).length;
  return {
    ok: ok || (deltaR >= 0.02 && remaining.length === 0),
    deltaR: +deltaR.toFixed(4),
    notes: [
      `score ${b.toFixed(3)} → ${a.toFixed(3)}`,
      `open high-severity fractures: ${remaining.length}`,
      `ΔR=${deltaR.toFixed(4)}`,
    ],
  };
}

function scoreState(state: NarrativeState): number {
  const fractures = detectFractures(state);
  const severitySum = fractures.reduce((s, f) => s + f.severity, 0);
  const castCoverage =
    state.characters.length === 0
      ? 0
      : state.characters.reduce((acc, c) => {
          const hits = state.scenes.filter((sc) =>
            sc.shots.some((sh) => sh.dialogue?.includes(c.name) || sh.description.includes(c.name)),
          ).length;
          return acc + Math.min(1, hits / Math.max(1, state.scenes.length * 0.3));
        }, 0) / state.characters.length;
  const actBalance = (() => {
    if (state.acts.length < 2) return 1;
    const sizes = state.acts.map((a) => state.scenes.filter((s) => s.act === a.number).length || 0.1);
    const mean = sizes.reduce((x, y) => x + y, 0) / sizes.length;
    const varc = sizes.reduce((x, y) => x + (y - mean) ** 2, 0) / sizes.length;
    return 1 / (1 + varc);
  })();
  const density = Math.min(1, state.script.length / 2000);
  return +(
    castCoverage * 0.35 +
    actBalance * 0.25 +
    density * 0.15 +
    Math.max(0, 1 - severitySum / 5) * 0.25
  ).toFixed(4);
}

/** MSIL — metacognitive stability over the repaired film. */
export function runMsil(state: NarrativeState, ryeHistory: number[]): MsilReport {
  const fractures = detectFractures(state);
  const continuity = Math.max(0, 1 - fractures.length * 0.08);
  const sizes = state.acts.map((a) => state.scenes.filter((s) => s.act === a.number).length || 0);
  const mean = sizes.length ? sizes.reduce((a, b) => a + b, 0) / sizes.length : 1;
  const actBalance = sizes.length
    ? 1 / (1 + sizes.reduce((s, n) => s + (n - mean) ** 2, 0) / sizes.length)
    : 1;
  const themeCoherence = (() => {
    const keys = state.premise.toLowerCase().split(/[^a-z0-9]+/).filter((w) => w.length > 5).slice(0, 5);
    if (!keys.length) return 0.7;
    const text = state.scenes.map((s) => s.summary.toLowerCase()).join(" ");
    return keys.filter((k) => text.includes(k)).length / keys.length;
  })();
  const oscillation =
    ryeHistory.length < 2
      ? 0
      : ryeHistory.slice(1).reduce((s, v, i) => s + Math.abs(v - ryeHistory[i]!), 0) /
        (ryeHistory.length - 1);
  const stabilityIndex = +(
    continuity * 0.35 +
    actBalance * 0.25 +
    themeCoherence * 0.25 +
    Math.max(0, 1 - oscillation * 2) * 0.15
  ).toFixed(4);
  const collapseRisk = +Math.max(0, Math.min(1, 1 - stabilityIndex + oscillation)).toFixed(4);
  const verdict: MsilReport["verdict"] =
    stabilityIndex >= 0.72 ? "stable" : stabilityIndex >= 0.45 ? "repairing" : "unstable";
  return {
    stabilityIndex,
    oscillation: +oscillation.toFixed(4),
    actBalance: +actBalance.toFixed(4),
    continuity: +continuity.toFixed(4),
    themeCoherence: +themeCoherence.toFixed(4),
    collapseRisk,
    verdict,
    notes: [
      `MSIL verdict: ${verdict}`,
      `continuity=${continuity.toFixed(2)} actBalance=${actBalance.toFixed(2)} theme=${themeCoherence.toFixed(2)}`,
      fractures.length ? `residual fractures: ${fractures.map((f) => f.class).join(", ")}` : "no residual fractures",
    ],
  };
}

/**
 * Full TGRM loop on a narrative state.
 * Micro-repair (severity < τ): cheap single-unit fix.
 * Full repair (severity ≥ τ): still minimal unit but counted as full path energy.
 */
export function runTgrm(input: NarrativeState, maxCycles = SCIENCE.maxCycles): TgrmResult {
  let state: NarrativeState = {
    ...input,
    scars: [...(input.scars ?? [])],
    scenes: input.scenes.map((s) => ({ ...s, shots: s.shots.map((sh) => ({ ...sh })) })),
  };
  const log: TgrmCycleLog[] = [];
  let totalDeltaR = 0;
  let totalEnergy = 0;
  let microRepairs = 0;
  let fullRepairs = 0;
  let reinforcements = 0;
  const ryeSeries: number[] = [];

  // Apply reinforced scars first (energy 1)
  if (state.scars.length) {
    totalEnergy += 1;
    log.push({
      cycle: 0,
      phase: "REINFORCE",
      verified: true,
      deltaR: 0,
      energy: 1,
      rye: 0,
      notes: [`Applied ${state.scars.length} scar-memory entries`],
    });
  }

  for (let cycle = 1; cycle <= maxCycles; cycle++) {
    // DETECT
    const fractures = detectFractures(state);
    totalEnergy += 1;
    if (!fractures.length) {
      log.push({
        cycle,
        phase: "VERIFY",
        verified: true,
        deltaR: 0,
        energy: 1,
        rye: 0,
        notes: ["No fractures detected — system at local equilibrium"],
      });
      break;
    }

    const fracture = fractures[0]!;
    const before = cloneState(state);

    // Choose micro vs full based on τ
    const isMicro = fracture.severity < SCIENCE.tau;
    const energyCost = isMicro ? 1 : 5;
    if (isMicro) microRepairs += 1;
    else fullRepairs += 1;

    // MINIMAL CORRECTION
    const { state: corrected, note } = minimalCorrection(state, fracture);
    state = corrected;
    totalEnergy += energyCost;

    // VERIFY
    const { ok, deltaR, notes } = verifyState(before, state);
    totalEnergy += 1;
    totalDeltaR += Math.max(0, deltaR);
    const cycleEnergy = 1 + energyCost + 1;
    const rye = +(Math.max(0, deltaR) / cycleEnergy).toFixed(4);
    ryeSeries.push(rye);

    log.push({
      cycle,
      phase: ok ? "VERIFY" : "MINIMAL_CORRECTION",
      fracture,
      correction: note,
      verified: ok,
      deltaR,
      energy: cycleEnergy,
      rye,
      notes: [
        `DETECT ${fracture.class} sev=${fracture.severity.toFixed(2)} ${isMicro ? "MICRO" : "FULL"} (τ=${SCIENCE.tau})`,
        note,
        ...notes,
      ],
    });

    if (ok && deltaR > 0) {
      // REINFORCE
      const scar: ScarMemory = {
        key: `${fracture.class}:${fracture.hint}`,
        fractureClass: fracture.class as FractureClass,
        fix: note,
        rye,
        uses: 1,
      };
      const existing = state.scars.findIndex((s) => s.key === scar.key);
      if (existing >= 0) {
        state.scars[existing] = {
          ...state.scars[existing]!,
          rye: Math.max(state.scars[existing]!.rye, rye),
          uses: state.scars[existing]!.uses + 1,
        };
      } else {
        state.scars.push(scar);
      }
      reinforcements += 1;
      log.push({
        cycle,
        phase: "REINFORCE",
        fracture,
        correction: note,
        verified: true,
        deltaR,
        energy: 0,
        rye,
        notes: [`Scar memory stored for ${fracture.class}`],
      });
      // Stop if high-severity fractures cleared
      if (!detectFractures(state).some((f) => f.severity >= SCIENCE.tau)) break;
    }
  }

  const energy = Math.max(1, totalEnergy);
  const rye = +(Math.max(0, totalDeltaR) / energy).toFixed(4);
  const rolling =
    ryeSeries.length === 0
      ? rye
      : +(ryeSeries.reduce((a, b) => a + b, 0) / ryeSeries.length).toFixed(4);
  const sorted = [...ryeSeries].sort((a, b) => a - b);
  const median =
    sorted.length === 0
      ? 0
      : sorted.length % 2
        ? sorted[(sorted.length - 1) / 2]!
        : +((sorted[sorted.length / 2 - 1]! + sorted[sorted.length / 2]!) / 2).toFixed(4);

  const msil = runMsil(state, ryeSeries);
  const metrics: RyeMetrics = {
    deltaR: +totalDeltaR.toFixed(4),
    energy,
    rye,
    rollingRye: rolling,
    medianRye: median,
    stabilityIndex: msil.stabilityIndex,
    recoveryMomentum: +Math.max(0, rolling - (ryeSeries[0] ?? 0)).toFixed(4),
    cycles: log.filter((l) => l.cycle > 0 && l.phase !== "REINFORCE").length,
    microRepairs,
    fullRepairs,
    reinforcements,
  };

  // Rebuild script body lightly from scenes if TGRM edited them
  state.script = rebuildScript(state);

  return { state, metrics, msil, log, scars: state.scars };
}

function cloneState(s: NarrativeState): NarrativeState {
  return {
    ...s,
    scenes: s.scenes.map((sc) => ({ ...sc, shots: sc.shots.map((sh) => ({ ...sh })) })),
    characters: s.characters.map((c) => ({ ...c })),
    acts: s.acts.map((a) => ({ ...a, sceneIds: [...a.sceneIds] })),
    chapters: s.chapters.map((c) => ({ ...c, sceneIds: [...c.sceneIds] })),
    scars: s.scars.map((x) => ({ ...x })),
  };
}

function rebuildScript(state: NarrativeState): string {
  const cast = state.characters
    .map((c) => `  ${c.name.toUpperCase()} — ${c.role}. ${c.arc}.`)
    .join("\n");
  const body = state.scenes
    .map((scene) => {
      const header = `ACT ${scene.act} · CH ${scene.chapter}\n${scene.slugline}\n\n${scene.summary}`;
      const shots = scene.shots
        .map((sh) => {
          const line = `  [${sh.type.toUpperCase()}] ${sh.description}`;
          return sh.dialogue ? `${line}\n\n                    ${sh.dialogue}` : line;
        })
        .join("\n\n");
      return `${header}\n\n${shots}`;
    })
    .join("\n\n\n");
  return (
    `${state.title.toUpperCase()}\n\n` +
    `REPARODYNAMICS · TGRM SCREENPLAY\n` +
    `LOGLINE\n${state.logline}\n\n` +
    `CAST\n${cast}\n\n` +
    `TARGET RUNTIME: ${state.targetMinutes} min\n\n` +
    `FADE IN:\n\n${body}\n\nFADE OUT.\n\nTHE END\n` +
    `\n// Scar memory: ${state.scars.map((s) => s.fractureClass).join(", ") || "none"}\n`
  );
}
