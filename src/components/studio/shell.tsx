import { Clapperboard, Activity } from "lucide-react";
import { cn } from "@/lib/utils";
import { STUDIO } from "@/lib/types";

export function StudioShell({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("relative min-h-dvh", className)}>
      <div
        className="pointer-events-none absolute inset-0 opacity-40"
        style={{
          background:
            "radial-gradient(ellipse 80% 50% at 50% -10%, color-mix(in oklab, var(--color-silver) 8%, transparent), transparent 70%)",
        }}
      />
      <header className="relative z-10 border-b border-border/80">
        <div className="mx-auto flex h-14 max-w-7xl items-center justify-between gap-4 px-4 sm:px-6">
          <div className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-md border border-border bg-surface">
              <Clapperboard className="h-4 w-4 text-silver" strokeWidth={1.5} />
            </div>
            <div className="leading-tight">
              <div className="font-display text-lg font-medium tracking-tight text-foreground">
                Silver-Screen
              </div>
              <div className="text-[10px] uppercase tracking-[0.16em] text-muted-foreground">
                {STUDIO.name} · {STUDIO.pipeline}
              </div>
            </div>
          </div>
          <div className="hidden items-center gap-2 text-xs text-muted-foreground sm:flex">
            <Activity className="h-3.5 w-3.5" strokeWidth={1.5} />
            <span>TGRM · RYE · MSIL · full-length cinema</span>
          </div>
        </div>
      </header>
      <main className="relative z-10 mx-auto max-w-7xl px-4 py-6 sm:px-6 sm:py-8">
        {children}
      </main>
    </div>
  );
}
