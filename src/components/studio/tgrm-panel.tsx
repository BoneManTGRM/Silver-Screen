import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { SCIENCE } from "@/lib/reparodynamics/science";
import type { FilmProject } from "@/lib/types";

export function TgrmPanel({ project }: { project: FilmProject }) {
  const rye = project.rye;
  const msil = project.msil;
  const log = project.tgrmLog ?? [];

  if (!rye && !msil) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>TGRM telemetry</CardTitle>
          <CardDescription>Run the script engine to populate RYE / MSIL metrics.</CardDescription>
        </CardHeader>
      </Card>
    );
  }

  return (
    <div className="grid gap-3 lg:grid-cols-3">
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">RYE</CardTitle>
          <CardDescription>{SCIENCE.rye} = ΔR / E</CardDescription>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          <Metric label="RYE" value={rye?.rye?.toFixed(4) ?? "—"} />
          <Metric label="ΔR" value={rye?.deltaR?.toFixed(4) ?? "—"} />
          <Metric label="Energy" value={String(rye?.energy ?? "—")} />
          <Metric label="Rolling RYE" value={rye?.rollingRye?.toFixed(4) ?? "—"} />
          <Metric label="Median RYE" value={rye?.medianRye?.toFixed(4) ?? "—"} />
          <Metric label="Micro / Full" value={`${rye?.microRepairs ?? 0} / ${rye?.fullRepairs ?? 0}`} />
          <Metric label="Reinforcements" value={String(rye?.reinforcements ?? 0)} />
          <p className="pt-1 text-[11px] text-muted-foreground">
            {`τ = ${SCIENCE.tau} · micro-repair when severity < τ`}
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">MSIL</CardTitle>
          <CardDescription>{SCIENCE.msil}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          <div className="flex items-center justify-between gap-2">
            <span className="text-muted-foreground">Verdict</span>
            <Badge
              variant={
                msil?.verdict === "stable"
                  ? "success"
                  : msil?.verdict === "repairing"
                    ? "warning"
                    : "muted"
              }
              className="capitalize"
            >
              {msil?.verdict ?? "—"}
            </Badge>
          </div>
          <Metric label="Stability" value={msil?.stabilityIndex?.toFixed(3) ?? "—"} />
          <Metric label="Continuity" value={msil?.continuity?.toFixed(3) ?? "—"} />
          <Metric label="Act balance" value={msil?.actBalance?.toFixed(3) ?? "—"} />
          <Metric label="Theme coherence" value={msil?.themeCoherence?.toFixed(3) ?? "—"} />
          <Metric label="Collapse risk" value={msil?.collapseRisk?.toFixed(3) ?? "—"} />
          <Metric label="Oscillation" value={msil?.oscillation?.toFixed(3) ?? "—"} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">TGRM cycles</CardTitle>
          <CardDescription>Detect → Minimal fix → Verify → Reinforce</CardDescription>
        </CardHeader>
        <CardContent className="max-h-56 space-y-2 overflow-y-auto text-xs">
          {log.length === 0 && <p className="text-muted-foreground">No cycles logged.</p>}
          {log.map((entry, i) => (
            <div
              key={`${entry.cycle}-${entry.phase}-${i}`}
              className="rounded-md border border-border bg-surface/60 p-2"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium text-foreground">
                  C{entry.cycle} · {entry.phase}
                </span>
                <span className="tabular-nums text-muted-foreground">
                  RYE {entry.rye.toFixed(3)}
                </span>
              </div>
              {entry.fracture && (
                <p className="mt-0.5 text-muted-foreground">
                  {entry.fracture.class} · sev {entry.fracture.severity.toFixed(2)}
                </p>
              )}
              {entry.correction && (
                <p className="mt-0.5 text-foreground/90">{entry.correction}</p>
              )}
            </div>
          ))}
          {(project.scars?.length ?? 0) > 0 && (
            <div className="pt-1">
              <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                Scar memory
              </div>
              <div className="mt-1 flex flex-wrap gap-1">
                {project.scars!.map((s) => (
                  <Badge key={s.key} variant="muted">
                    {s.fractureClass} ×{s.uses}
                  </Badge>
                ))}
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-mono tabular-nums text-foreground">{value}</span>
    </div>
  );
}
