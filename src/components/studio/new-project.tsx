import { useState } from "react";
import { Sparkles, Wand2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { FORMATS, GENRES, STUDIO, TONES, type FilmFormat, type Genre, type Tone } from "@/lib/types";
import { SCIENCE, FIVE_LAWS } from "@/lib/reparodynamics/science";
import { useStudio } from "@/store/studio";
import { cn } from "@/lib/utils";

const PRESETS = [
  {
    title: "Ash Signal",
    premise:
      "A cyberpunk detective in Mexico City traces a stolen neural map through rain-slick neon districts before the city forgets itself.",
    genre: "noir" as Genre,
    tone: "cinematic" as Tone,
    format: "feature" as FilmFormat,
  },
  {
    title: "Quiet Orbit",
    premise:
      "A rogue pilot and an aging AI companion race to deliver a sealed cargo across a dying orbital ring while the syndicate rewrites gravity.",
    genre: "scifi" as Genre,
    tone: "tense" as Tone,
    format: "episode" as FilmFormat,
  },
  {
    title: "Near Miss",
    premise:
      "Two strangers keep almost meeting on the same ferry for a year — until a storm forces them into one shared cabin and one hard truth.",
    genre: "romance" as Genre,
    tone: "intimate" as Tone,
    format: "short" as FilmFormat,
  },
];

export function NewProjectPanel() {
  const createProject = useStudio((s) => s.createProject);
  const runPipeline = useStudio((s) => s.runPipeline);
  const isWorking = useStudio((s) => s.isWorking);

  const [premise, setPremise] = useState(PRESETS[0]!.premise);
  const [title, setTitle] = useState("");
  const [genre, setGenre] = useState<Genre>("noir");
  const [tone, setTone] = useState<Tone>("cinematic");
  const [format, setFormat] = useState<FilmFormat>("feature");
  const [busy, setBusy] = useState(false);

  async function handleCreate(fullPipeline: boolean) {
    if (!premise.trim()) {
      toast.error("Write a movie idea first");
      return;
    }
    setBusy(true);
    try {
      const id = createProject({
        premise,
        genre,
        tone,
        format,
        title: title.trim() || undefined,
      });
      toast.success(fullPipeline ? "TGRM pipeline started" : "Project created · TGRM scripted");
      if (fullPipeline) {
        await runPipeline(id);
        toast.success("Film packaged under Reparodynamics · TGRM");
      }
    } catch (e) {
      console.error(e);
      toast.error("TGRM pipeline failed — check console");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="overflow-hidden border-border/80">
      <CardHeader className="border-b border-border/60 bg-surface/40">
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="text-2xl">New film</CardTitle>
            <CardDescription className="mt-1 max-w-2xl">
              {STUDIO.tagline} Powered by {SCIENCE.pipelineFull} (τ={SCIENCE.tau}),{" "}
              {SCIENCE.rye}, and {SCIENCE.msil}. Full-length formats scale acts, chapters, and
              TGRM repair cycles.
            </CardDescription>
          </div>
          <Badge variant="silver" className="shrink-0">
            {STUDIO.name}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-6 p-5">
        <div className="space-y-2">
          <Label htmlFor="premise">Movie idea</Label>
          <Textarea
            id="premise"
            value={premise}
            onChange={(e) => setPremise(e.target.value)}
            placeholder="A cyberpunk detective in Mexico City…"
            className="min-h-28 text-base"
          />
        </div>

        <div className="space-y-2">
          <Label>Format · runtime target</Label>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
            {FORMATS.map((f) => (
              <button
                key={f.id}
                type="button"
                onClick={() => setFormat(f.id)}
                className={cn(
                  "rounded-lg border px-3 py-3 text-left transition-colors",
                  format === f.id
                    ? "border-silver-edge/60 bg-silver/10"
                    : "border-border bg-surface hover:bg-surface-hover",
                )}
              >
                <div className="text-sm font-medium text-foreground">{f.label}</div>
                <div className="mt-0.5 text-[11px] text-muted-foreground">
                  {f.minutes}m · {f.scenes} scenes
                </div>
              </button>
            ))}
          </div>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="title">Title (optional)</Label>
            <Input
              id="title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Leave blank to invent one"
            />
          </div>
          <div className="space-y-2">
            <Label>Tone</Label>
            <div className="flex flex-wrap gap-1.5">
              {TONES.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => setTone(t.id)}
                  className={cn(
                    "rounded-full border px-3 py-1.5 text-xs font-medium transition-colors",
                    tone === t.id
                      ? "border-silver-edge bg-silver text-ink"
                      : "border-border bg-surface text-muted-foreground hover:text-foreground",
                  )}
                >
                  {t.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="space-y-2">
          <Label>Genre</Label>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {GENRES.map((g) => (
              <button
                key={g.id}
                type="button"
                onClick={() => setGenre(g.id)}
                className={cn(
                  "rounded-lg border px-3 py-3 text-left transition-colors",
                  genre === g.id
                    ? "border-silver-edge/60 bg-silver/10"
                    : "border-border bg-surface hover:bg-surface-hover",
                )}
              >
                <div className="text-sm font-medium text-foreground">{g.label}</div>
                <div className="mt-0.5 text-[11px] text-muted-foreground">{g.hint}</div>
              </button>
            ))}
          </div>
        </div>

        <div className="rounded-lg border border-border bg-surface/50 p-4">
          <div className="text-xs uppercase tracking-[0.14em] text-muted-foreground">
            Five-law cinema · Reparodynamics
          </div>
          <ul className="mt-2 grid gap-1.5 sm:grid-cols-2">
            {FIVE_LAWS.map((law) => (
              <li key={law.id} className="text-xs text-muted-foreground">
                <span className="font-medium text-foreground">{law.name}.</span> {law.cinema}
              </li>
            ))}
          </ul>
        </div>

        <div className="space-y-2">
          <Label>Quick presets</Label>
          <div className="flex flex-col gap-2 sm:flex-row">
            {PRESETS.map((p) => (
              <button
                key={p.title}
                type="button"
                onClick={() => {
                  setPremise(p.premise);
                  setTitle(p.title);
                  setGenre(p.genre);
                  setTone(p.tone);
                  setFormat(p.format);
                }}
                className="flex-1 rounded-lg border border-border bg-surface px-3 py-2.5 text-left text-sm transition-colors hover:bg-surface-hover"
              >
                <span className="font-medium text-foreground">{p.title}</span>
                <span className="mt-0.5 block text-[11px] text-muted-foreground">
                  {p.format} · {p.premise.slice(0, 60)}…
                </span>
              </button>
            ))}
          </div>
        </div>

        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <Button
            size="lg"
            variant="silver"
            className="sm:flex-1"
            disabled={busy || isWorking}
            onClick={() => handleCreate(true)}
          >
            <Wand2 className="h-4 w-4" />
            Run TGRM full pipeline
          </Button>
          <Button
            size="lg"
            variant="outline"
            className="sm:flex-1"
            disabled={busy || isWorking}
            onClick={() => handleCreate(false)}
          >
            <Sparkles className="h-4 w-4" />
            Script + board only
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
