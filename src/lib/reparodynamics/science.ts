/**
 * Reparodynamics open-science constants for Silver-Screen.
 * Provenance: Cody Ryan Jenkins / BoneManTGRM
 * - TGRM: Targeted Gradient Repair Mechanism (Zenodo 10.5281/zenodo.17273433)
 * - RYE: Repair Yield per Energy
 * - Reparodynamics five-law system (Zenodo 10.5281/zenodo.17538091)
 * - MSIL: Meta Stability Intelligence Layer (ARA)
 */

export const SCIENCE = {
  studio: "Reparodynamics",
  founder: "Cody Ryan Jenkins",
  pipeline: "TGRM",
  pipelineFull: "Targeted Gradient Repair Mechanism",
  rye: "Repair Yield per Energy",
  msil: "Meta Stability Intelligence Layer",
  ara: "Autonomous Research Agent",
  /** Adaptive micro-repair threshold from TGRM sims (τ ≈ 0.6) */
  tau: 0.6,
  maxCycles: 5,
  /** Minimum RYE to accept a repaired state */
  ryeAccept: 0.12,
  domainGate: 0.65,
  dois: {
    fiveLaw: "10.5281/zenodo.17538091",
    tgrm: "10.5281/zenodo.17273433",
    codingVelocity: "10.5281/zenodo.17336075",
  },
  github: "https://github.com/BoneManTGRM/Silver-Screen",
  corpus: "https://bonemantgrm.github.io/reparodynamics-corpus/",
  x: "https://x.com/Reparodynamics",
  credit: "A Reparodynamics Production · TGRM · RYE · MSIL",
  tagline:
    "Self-repairing narrative systems — detect fractures, apply minimal corrections, verify continuity, reinforce scar memory.",
  loop: ["DETECT", "MINIMAL_CORRECTION", "VERIFY", "REINFORCE"] as const,
} as const;

/** Five-law framing applied to cinema (aligned self-repair under energy bounds). */
export const FIVE_LAWS = [
  {
    id: "bounded_energy",
    name: "Energy-Bounded Repair",
    cinema: "Never over-write a scene when a micro-fix restores continuity.",
  },
  {
    id: "minimal_gradient",
    name: "Minimal Gradient",
    cinema: "Change the smallest narrative unit (one beat, one line, one motive).",
  },
  {
    id: "verified_delta",
    name: "Verified ΔR",
    cinema: "Only keep edits that improve measurable continuity / tension / clarity.",
  },
  {
    id: "scar_memory",
    name: "Scar Memory",
    cinema: "Reinforce winning fixes so later acts inherit repaired character logic.",
  },
  {
    id: "aligned_stability",
    name: "Aligned Stability",
    cinema: "MSIL rejects repairs that raise RYE locally but collapse global act structure.",
  },
] as const;

export type FractureClass =
  | "plot_hole"
  | "character_drift"
  | "timeline_break"
  | "theme_noise"
  | "pacing_collapse"
  | "dialogue_redundancy"
  | "act_imbalance";

export interface Fracture {
  id: string;
  class: FractureClass;
  severity: number; // 0..1 — compared against τ
  location: string;
  description: string;
  hint: string;
}

export interface TgrmCycleLog {
  cycle: number;
  phase: (typeof SCIENCE.loop)[number] | "IDLE";
  fracture?: Fracture;
  correction?: string;
  verified: boolean;
  deltaR: number;
  energy: number;
  rye: number;
  notes: string[];
}

export interface RyeMetrics {
  deltaR: number;
  energy: number;
  rye: number;
  rollingRye: number;
  medianRye: number;
  stabilityIndex: number;
  recoveryMomentum: number;
  cycles: number;
  microRepairs: number;
  fullRepairs: number;
  reinforcements: number;
}

export interface MsilReport {
  stabilityIndex: number;
  oscillation: number;
  actBalance: number;
  continuity: number;
  themeCoherence: number;
  collapseRisk: number;
  verdict: "stable" | "repairing" | "unstable";
  notes: string[];
}

export interface ScarMemory {
  key: string;
  fractureClass: FractureClass;
  fix: string;
  rye: number;
  uses: number;
}
