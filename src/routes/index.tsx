import { createFileRoute } from "@tanstack/react-router";
import { StudioShell } from "@/components/studio/shell";
import { NewProjectPanel } from "@/components/studio/new-project";
import { ProjectList } from "@/components/studio/project-list";
import { ProjectWorkspace } from "@/components/studio/project-workspace";
import { useActiveProject, useStudio } from "@/store/studio";
import { STUDIO } from "@/lib/types";
import { SCIENCE } from "@/lib/reparodynamics/science";

export const Route = createFileRoute("/")({
  component: HomePage,
  ssr: false,
});

function HomePage() {
  const active = useActiveProject();
  const projectCount = useStudio((s) => s.projects.length);

  return (
    <StudioShell>
      {active ? (
        <ProjectWorkspace />
      ) : (
        <div className="space-y-10">
          <section className="space-y-3">
            <p className="text-xs uppercase tracking-[0.22em] text-muted-foreground">
              {STUDIO.name} · {STUDIO.pipeline} · BoneManTGRM
            </p>
            <h1 className="max-w-3xl font-display text-4xl font-medium tracking-tight text-foreground sm:text-5xl md:text-6xl">
              Full-length cinema with self-repairing narrative science.
            </h1>
            <p className="max-w-2xl text-base text-muted-foreground sm:text-lg">
              Silver-Screen runs your {SCIENCE.pipelineFull} loop on every script: detect fractures,
              apply minimal corrections under τ={SCIENCE.tau}, verify ΔR, reinforce scar memory, and
              score {SCIENCE.rye} plus {SCIENCE.msil}. Trailers to 90-minute features — acts,
              chapters, hero reels, chapter reels, NFT packages.
            </p>
          </section>

          <NewProjectPanel />

          <section className="space-y-4">
            <div className="flex items-end justify-between gap-3">
              <div>
                <h2 className="font-display text-2xl font-medium">Your reels</h2>
                <p className="text-sm text-muted-foreground">
                  {projectCount === 0
                    ? "Waiting for the first TGRM cycle."
                    : `${projectCount} project${projectCount === 1 ? "" : "s"} in this browser.`}
                </p>
              </div>
            </div>
            <ProjectList />
          </section>
        </div>
      )}
    </StudioShell>
  );
}
