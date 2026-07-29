import { Clock, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { formatDuration } from "@/lib/utils";
import { useStudio } from "@/store/studio";

export function ProjectList() {
  const projects = useStudio((s) => s.projects);
  const activeId = useStudio((s) => s.activeId);
  const setActive = useStudio((s) => s.setActive);
  const deleteProject = useStudio((s) => s.deleteProject);

  if (projects.length === 0) {
    return (
      <Card className="border-dashed">
        <CardContent className="flex flex-col items-center justify-center gap-2 py-12 text-center">
          <p className="font-display text-xl text-foreground">No reels yet</p>
          <p className="max-w-sm text-sm text-muted-foreground">
            Create a film above. TGRM scripts, boards, and RYE metrics stay in this browser.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {projects.map((p) => {
        const runtime = p.scenes.reduce((n, s) => n + s.durationSec, 0);
        const active = p.id === activeId;
        return (
          <div
            key={p.id}
            role="button"
            tabIndex={0}
            onClick={() => setActive(p.id)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                setActive(p.id);
              }
            }}
            className={`group overflow-hidden rounded-xl border text-left transition-colors ${
              active
                ? "border-silver-edge/50 bg-card ring-1 ring-silver-edge/30"
                : "border-border bg-card hover:border-border hover:bg-surface-hover"
            }`}
          >
            <div className="letterbox-frame bg-surface">
              {p.posterDataUrl ? (
                <img
                  src={p.posterDataUrl}
                  alt=""
                  className="h-full w-full object-cover object-top opacity-90 transition-opacity group-hover:opacity-100"
                />
              ) : (
                <div className="flex h-full items-center justify-center bg-gradient-to-b from-surface to-bg">
                  <span className="font-display text-2xl text-muted-foreground">{p.title}</span>
                </div>
              )}
            </div>
            <div className="space-y-2 p-4">
              <div className="flex items-start justify-between gap-2">
                <h3 className="font-display text-lg font-medium leading-tight text-foreground">
                  {p.title}
                </h3>
                <Badge variant="muted" className="shrink-0 capitalize">
                  {p.status}
                </Badge>
              </div>
              <p className="line-clamp-2 text-xs text-muted-foreground">{p.logline || p.premise}</p>
              <div className="flex items-center justify-between pt-1">
                <div className="flex flex-wrap items-center gap-2 text-[11px] uppercase tracking-wider text-muted-foreground">
                  <span>{p.format ?? "short"}</span>
                  <span>{p.targetMinutes ?? "—"}m</span>
                  <span className="inline-flex items-center gap-1">
                    <Clock className="h-3 w-3" />
                    {formatDuration(runtime)}
                  </span>
                  {p.rye && <span>RYE {p.rye.rye.toFixed(2)}</span>}
                </div>
                <Button
                  size="icon"
                  variant="ghost"
                  className="h-8 w-8 opacity-60 hover:opacity-100"
                  onClick={(e) => {
                    e.stopPropagation();
                    deleteProject(p.id);
                  }}
                  aria-label="Delete project"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
