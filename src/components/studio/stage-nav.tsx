import { STAGES, type FilmStage } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useStudio } from "@/store/studio";

export function StageNav() {
  const stage = useStudio((s) => s.stage);
  const setStage = useStudio((s) => s.setStage);

  return (
    <nav className="flex gap-1 overflow-x-auto pb-1" aria-label="Studio stages">
      {STAGES.map((s) => (
        <StageButton
          key={s.id}
          step={s.step}
          label={s.label}
          active={stage === s.id}
          onClick={() => setStage(s.id as FilmStage)}
        />
      ))}
    </nav>
  );
}

function StageButton({
  step,
  label,
  active,
  onClick,
}: {
  step: number;
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex min-h-11 min-w-[4.5rem] flex-col items-center justify-center rounded-lg border px-3 py-2 transition-colors",
        active
          ? "border-silver-edge/50 bg-silver/10 text-foreground"
          : "border-transparent bg-transparent text-muted-foreground hover:bg-surface-hover hover:text-foreground",
      )}
    >
      <span className="text-[10px] tabular-nums tracking-widest opacity-70">
        {String(step).padStart(2, "0")}
      </span>
      <span className="text-xs font-medium uppercase tracking-[0.12em]">{label}</span>
    </button>
  );
}
