import { create } from "zustand";
import { persist } from "zustand/middleware";
import { uid } from "@/lib/utils";
import { buildFilmFromBrief } from "@/lib/script-engine";
import { boardScenes, paintPoster } from "@/lib/storyboard";
import { buildNftMetadata, renderChapter, renderFilm } from "@/lib/renderer";
import type { FilmFormat, FilmProject, FilmStage, Genre, Tone } from "@/lib/types";

interface StudioState {
  projects: FilmProject[];
  activeId: string | null;
  stage: FilmStage;
  isWorking: boolean;
  createProject: (input: {
    premise: string;
    genre: Genre;
    tone: Tone;
    title?: string;
    format?: FilmFormat;
  }) => string;
  setActive: (id: string | null) => void;
  setStage: (stage: FilmStage) => void;
  updateProject: (id: string, patch: Partial<FilmProject>) => void;
  deleteProject: (id: string) => void;
  runPipeline: (id: string) => Promise<void>;
  generateScriptOnly: (id: string) => void;
  generateBoardOnly: (id: string) => void;
  renderOnly: (id: string) => Promise<void>;
  renderAllChapters: (id: string) => Promise<void>;
  packageNft: (id: string) => void;
}

function now() {
  return new Date().toISOString();
}

export const useStudio = create<StudioState>()(
  persist(
    (set, get) => ({
      projects: [],
      activeId: null,
      stage: "brief",
      isWorking: false,

      createProject: (input) => {
        const id = uid("film");
        const built = buildFilmFromBrief(input);
        const project: FilmProject = {
          id,
          premise: input.premise.trim(),
          genre: input.genre,
          tone: input.tone,
          title: built.title,
          logline: built.logline,
          script: built.script,
          characters: built.characters,
          scenes: built.scenes,
          acts: built.acts,
          chapters: built.chapters,
          format: built.format,
          targetMinutes: built.targetMinutes,
          rye: built.rye,
          msil: built.msil,
          tgrmLog: built.tgrmLog,
          scars: built.scars,
          studio: "Reparodynamics",
          pipeline: "TGRM",
          createdAt: now(),
          updatedAt: now(),
          status: "scripted",
          pipelineProgress: 30,
          pipelineLabel: "TGRM script + RYE verified",
        };
        if (typeof document !== "undefined") {
          project.posterDataUrl = paintPoster({
            title: project.title,
            genre: project.genre,
            logline: project.logline,
          });
          project.scenes = boardScenes(
            project.scenes,
            project.genre,
            project.tone,
            project.title,
          );
          project.status = "boarded";
          project.pipelineProgress = 55;
          project.pipelineLabel = "Storyboard painted · scar memory ready";
        }
        set((s) => ({
          projects: [project, ...s.projects],
          activeId: id,
          stage: "script",
        }));
        return id;
      },

      setActive: (id) => set({ activeId: id, stage: id ? "script" : "brief" }),
      setStage: (stage) => set({ stage }),

      updateProject: (id, patch) =>
        set((s) => ({
          projects: s.projects.map((p) =>
            p.id === id ? { ...p, ...patch, updatedAt: now() } : p,
          ),
        })),

      deleteProject: (id) =>
        set((s) => ({
          projects: s.projects.filter((p) => p.id !== id),
          activeId: s.activeId === id ? null : s.activeId,
        })),

      generateScriptOnly: (id) => {
        const p = get().projects.find((x) => x.id === id);
        if (!p) return;
        const built = buildFilmFromBrief({
          premise: p.premise,
          genre: p.genre,
          tone: p.tone,
          title: p.title,
          format: p.format,
          scars: p.scars,
        });
        get().updateProject(id, {
          ...built,
          pipelineProgress: 35,
          pipelineLabel: "TGRM re-ran · RYE updated",
        });
        set({ stage: "script" });
      },

      generateBoardOnly: (id) => {
        const p = get().projects.find((x) => x.id === id);
        if (!p || typeof document === "undefined") return;
        const scenes = boardScenes(p.scenes, p.genre, p.tone, p.title);
        const posterDataUrl = paintPoster({
          title: p.title,
          genre: p.genre,
          logline: p.logline,
        });
        get().updateProject(id, {
          scenes,
          posterDataUrl,
          status: "boarded",
          pipelineProgress: 60,
          pipelineLabel: "Storyboard painted",
        });
        set({ stage: "storyboard" });
      },

      renderOnly: async (id) => {
        const p = get().projects.find((x) => x.id === id);
        if (!p) return;
        set({ isWorking: true, stage: "render" });
        try {
          let project = p;
          if (!p.scenes.some((s) => s.shots.some((sh) => sh.frameDataUrl))) {
            const scenes = boardScenes(p.scenes, p.genre, p.tone, p.title);
            get().updateProject(id, { scenes });
            project = { ...p, scenes };
          }
          const url = await renderFilm(project, (pct, label) => {
            get().updateProject(id, { pipelineProgress: pct, pipelineLabel: label });
          });
          get().updateProject(id, {
            renderUrl: url,
            status: "rendered",
            pipelineProgress: 100,
            pipelineLabel: "Hero reel rendered (TGRM)",
          });
        } finally {
          set({ isWorking: false });
        }
      },

      renderAllChapters: async (id) => {
        const p = get().projects.find((x) => x.id === id);
        if (!p) return;
        set({ isWorking: true, stage: "render" });
        try {
          let project = p;
          if (!p.scenes.some((s) => s.shots.some((sh) => sh.frameDataUrl))) {
            const scenes = boardScenes(p.scenes, p.genre, p.tone, p.title);
            get().updateProject(id, { scenes });
            project = { ...p, scenes };
          }
          const chapters = [...project.chapters];
          for (let i = 0; i < chapters.length; i++) {
            const ch = chapters[i]!;
            const url = await renderChapter(project, ch.number, (pct, label) => {
              const overall = Math.round(((i + pct / 100) / chapters.length) * 100);
              get().updateProject(id, {
                pipelineProgress: overall,
                pipelineLabel: label,
              });
            });
            chapters[i] = { ...ch, renderUrl: url };
            get().updateProject(id, { chapters: [...chapters] });
          }
          // Also produce hero reel if missing
          if (!get().projects.find((x) => x.id === id)?.renderUrl) {
            const url = await renderFilm(
              get().projects.find((x) => x.id === id)!,
              (pct, label) => {
                get().updateProject(id, {
                  pipelineProgress: Math.min(99, pct),
                  pipelineLabel: label,
                });
              },
            );
            get().updateProject(id, { renderUrl: url });
          }
          get().updateProject(id, {
            status: "rendered",
            pipelineProgress: 100,
            pipelineLabel: `All ${chapters.length} chapters rendered · full-length package ready`,
          });
        } finally {
          set({ isWorking: false });
        }
      },

      packageNft: (id) => {
        const p = get().projects.find((x) => x.id === id);
        if (!p) return;
        const nftMetadata = buildNftMetadata(p);
        get().updateProject(id, {
          nftMetadata,
          status: "packaged",
          pipelineProgress: 100,
          pipelineLabel: "NFT package sealed · Reparodynamics",
        });
        set({ stage: "nft" });
      },

      runPipeline: async (id) => {
        const p = get().projects.find((x) => x.id === id);
        if (!p) return;
        set({ isWorking: true });
        try {
          get().updateProject(id, {
            pipelineProgress: 8,
            pipelineLabel: "TGRM DETECT — scanning fracture field…",
          });
          await delay(300);
          const built = buildFilmFromBrief({
            premise: p.premise,
            genre: p.genre,
            tone: p.tone,
            title: p.title,
            format: p.format,
            scars: p.scars,
          });
          get().updateProject(id, {
            ...built,
            pipelineProgress: 28,
            pipelineLabel: `TGRM VERIFY — RYE ${built.rye?.rye ?? 0} · MSIL ${built.msil?.verdict}`,
          });
          set({ stage: "script" });
          await delay(280);

          get().updateProject(id, {
            pipelineProgress: 42,
            pipelineLabel: "Painting storyboard under energy bounds…",
          });
          set({ stage: "storyboard" });
          const latest = get().projects.find((x) => x.id === id)!;
          const scenes = boardScenes(
            latest.scenes,
            latest.genre,
            latest.tone,
            latest.title,
          );
          const posterDataUrl = paintPoster({
            title: latest.title,
            genre: latest.genre,
            logline: latest.logline,
          });
          get().updateProject(id, {
            scenes,
            posterDataUrl,
            status: "boarded",
            pipelineProgress: 58,
            pipelineLabel: "Recording TGRM hero reel…",
          });
          set({ stage: "render" });
          await delay(150);

          const forRender = get().projects.find((x) => x.id === id)!;
          const url = await renderFilm(forRender, (pct, label) => {
            get().updateProject(id, {
              pipelineProgress: Math.min(88, 58 + pct * 0.3),
              pipelineLabel: label,
            });
          });

          // For feature/episode, also render chapters (capped for time on trailer/short)
          let chapters = forRender.chapters;
          if (forRender.format !== "trailer") {
            get().updateProject(id, {
              pipelineProgress: 90,
              pipelineLabel: "Chapter reels — full-length assembly…",
            });
            const next = [...chapters];
            // Render up to 3 chapters in full pipeline for speed; user can render all later
            const limit = Math.min(next.length, forRender.format === "feature" ? 3 : next.length);
            for (let i = 0; i < limit; i++) {
              const chUrl = await renderChapter(
                { ...forRender, scenes, renderUrl: url },
                next[i]!.number,
                (pct, label) => {
                  get().updateProject(id, {
                    pipelineProgress: Math.min(96, 90 + ((i + pct / 100) / limit) * 6),
                    pipelineLabel: label,
                  });
                },
              );
              next[i] = { ...next[i]!, renderUrl: chUrl };
            }
            chapters = next;
          }

          get().updateProject(id, {
            renderUrl: url,
            chapters,
            status: "rendered",
            pipelineProgress: 97,
            pipelineLabel: "Sealing NFT + RYE attributes…",
          });
          await delay(200);
          const packaged = get().projects.find((x) => x.id === id)!;
          const nftMetadata = buildNftMetadata({ ...packaged, renderUrl: url });
          get().updateProject(id, {
            nftMetadata,
            status: "packaged",
            pipelineProgress: 100,
            pipelineLabel: "Complete — Reparodynamics · TGRM",
          });
          set({ stage: "nft" });
        } finally {
          set({ isWorking: false });
        }
      },
    }),
    {
      name: "silver-screen-tgrm-v2",
      partialize: (s) => ({
        projects: s.projects.map((p) => ({
          ...p,
          scenes: p.scenes.map((sc) => ({
            ...sc,
            shots: sc.shots.map(({ frameDataUrl: _f, ...shot }) => shot),
          })),
          renderUrl: undefined,
          chapters: p.chapters.map((c) => ({ ...c, renderUrl: undefined })),
        })),
        activeId: s.activeId,
      }),
    },
  ),
);

function delay(ms: number) {
  return new Promise((r) => setTimeout(r, ms));
}

export function useActiveProject() {
  return useStudio((s) => s.projects.find((p) => p.id === s.activeId) ?? null);
}
