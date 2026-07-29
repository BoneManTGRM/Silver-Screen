import {
  Download,
  Film,
  Layers,
  Mic,
  Package,
  Play,
  RefreshCw,
  Wand2,
} from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import { StageNav } from "@/components/studio/stage-nav";
import { TgrmPanel } from "@/components/studio/tgrm-panel";
import { downloadJson, downloadUrl, speakNarration } from "@/lib/renderer";
import { formatDuration } from "@/lib/utils";
import { useActiveProject, useStudio } from "@/store/studio";

export function ProjectWorkspace() {
  const project = useActiveProject();
  const stage = useStudio((s) => s.stage);
  const isWorking = useStudio((s) => s.isWorking);
  const updateProject = useStudio((s) => s.updateProject);
  const runPipeline = useStudio((s) => s.runPipeline);
  const generateScriptOnly = useStudio((s) => s.generateScriptOnly);
  const generateBoardOnly = useStudio((s) => s.generateBoardOnly);
  const renderOnly = useStudio((s) => s.renderOnly);
  const renderAllChapters = useStudio((s) => s.renderAllChapters);
  const packageNft = useStudio((s) => s.packageNft);
  const setActive = useStudio((s) => s.setActive);

  if (!project) return null;

  const runtime = project.scenes.reduce((n, s) => n + s.durationSec, 0);
  const shots = project.scenes.reduce((n, s) => n + s.shots.length, 0);

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div className="space-y-2">
          <button
            type="button"
            onClick={() => setActive(null)}
            className="text-xs uppercase tracking-[0.14em] text-muted-foreground hover:text-foreground"
          >
            All projects
          </button>
          <h1 className="font-display text-3xl font-medium tracking-tight sm:text-4xl">
            {project.title}
          </h1>
          <p className="max-w-2xl text-sm text-muted-foreground">{project.logline}</p>
          <div className="flex flex-wrap items-center gap-2 pt-1">
            <Badge variant="silver" className="capitalize">
              {project.genre}
            </Badge>
            <Badge variant="muted" className="capitalize">
              {project.tone}
            </Badge>
            <Badge variant="default" className="capitalize">
              {project.format} · {project.targetMinutes}m
            </Badge>
            <Badge variant="default">{project.acts?.length ?? 0} acts</Badge>
            <Badge variant="default">{project.chapters?.length ?? 0} ch</Badge>
            <Badge variant="default">{project.scenes.length} scenes</Badge>
            <Badge variant="default">{shots} shots</Badge>
            <Badge variant="default">reel {formatDuration(runtime)}</Badge>
            {project.rye && (
              <Badge variant="success">RYE {project.rye.rye.toFixed(3)}</Badge>
            )}
            {project.msil && (
              <Badge variant="warning" className="capitalize">
                MSIL {project.msil.verdict}
              </Badge>
            )}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="silver"
            disabled={isWorking}
            onClick={async () => {
              await runPipeline(project.id);
              toast.success("Full pipeline complete");
            }}
          >
            <Wand2 className="h-4 w-4" />
            Run TGRM pipeline
          </Button>
        </div>
      </div>

      <div className="space-y-2">
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span>{project.pipelineLabel || "Ready"}</span>
          <span className="tabular-nums">{Math.round(project.pipelineProgress)}%</span>
        </div>
        <Progress value={project.pipelineProgress} />
      </div>

      <StageNav />

      <TgrmPanel project={project} />

      {stage === "brief" && (
        <Card>
          <CardHeader>
            <CardTitle>Brief</CardTitle>
            <CardDescription>Source premise that drives the entire film.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <Textarea
              value={project.premise}
              onChange={(e) => updateProject(project.id, { premise: e.target.value })}
              className="min-h-32"
            />
            <div className="grid gap-3 sm:grid-cols-2">
              {project.posterDataUrl && (
                <img
                  src={project.posterDataUrl}
                  alt="Poster"
                  className="aspect-[2/3] w-full max-w-xs rounded-lg border border-border object-cover"
                />
              )}
              <div className="space-y-3 text-sm text-muted-foreground">
                <p>
                  <span className="text-foreground">Logline: </span>
                  {project.logline}
                </p>
                <p>
                  <span className="text-foreground">Status: </span>
                  <span className="capitalize">{project.status}</span>
                </p>
                <Button
                  variant="outline"
                  onClick={() => {
                    generateScriptOnly(project.id);
                    toast.success("Script rebuilt from brief");
                  }}
                >
                  <RefreshCw className="h-4 w-4" />
                  Rebuild from brief
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {stage === "script" && (
        <Card>
          <CardHeader className="flex-row items-start justify-between gap-3 space-y-0">
            <div>
              <CardTitle>Screenplay</CardTitle>
              <CardDescription>Editable local generation — industry-style formatting.</CardDescription>
            </div>
            <div className="flex gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={() => {
                  speakNarration(project.logline + ". " + project.scenes[0]?.summary);
                  toast.message("Narrating logline");
                }}
              >
                <Mic className="h-3.5 w-3.5" />
                Narrate
              </Button>
              <Button
                size="sm"
                variant="secondary"
                onClick={() => {
                  generateScriptOnly(project.id);
                  toast.success("Script regenerated");
                }}
              >
                <RefreshCw className="h-3.5 w-3.5" />
                Regenerate
              </Button>
            </div>
          </CardHeader>
          <CardContent>
            <Textarea
              value={project.script}
              onChange={(e) => updateProject(project.id, { script: e.target.value })}
              className="screenplay min-h-[28rem] bg-surface/60 font-mono text-[13px] leading-relaxed"
            />
          </CardContent>
        </Card>
      )}

      {stage === "cast" && (
        <div className="grid gap-3 sm:grid-cols-3">
          {project.characters.map((c) => (
            <Card key={c.id}>
              <CardHeader>
                <CardTitle className="text-xl">{c.name}</CardTitle>
                <CardDescription>{c.role}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-2 text-sm">
                <p className="text-muted-foreground">{c.description}</p>
                <Separator />
                <p>
                  <span className="text-xs uppercase tracking-wider text-muted-foreground">
                    Arc
                  </span>
                  <br />
                  {c.arc}
                </p>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {stage === "storyboard" && (
        <div className="space-y-4">
          <div className="flex justify-end">
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                generateBoardOnly(project.id);
                toast.success("Storyboard repainted");
              }}
            >
              <RefreshCw className="h-3.5 w-3.5" />
              Repaint boards
            </Button>
          </div>
          {project.scenes.map((scene) => (
            <Card key={scene.id}>
              <CardHeader className="pb-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <CardTitle className="font-mono text-sm tracking-wide">
                    {scene.slugline}
                  </CardTitle>
                  <Badge variant="muted">{formatDuration(scene.durationSec)}</Badge>
                </div>
                <CardDescription>{scene.summary}</CardDescription>
              </CardHeader>
              <CardContent>
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                  {scene.shots.map((shot) => (
                    <div
                      key={shot.id}
                      className="overflow-hidden rounded-lg border border-border bg-surface"
                    >
                      <div className="letterbox-frame">
                        {shot.frameDataUrl ? (
                          <img
                            src={shot.frameDataUrl}
                            alt=""
                            className="h-full w-full object-cover"
                          />
                        ) : (
                          <div className="flex h-full items-center justify-center text-xs text-muted-foreground">
                            No frame — repaint boards
                          </div>
                        )}
                      </div>
                      <div className="space-y-1 p-2.5">
                        <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                          {shot.type} · {shot.durationSec.toFixed(1)}s
                        </div>
                        <p className="line-clamp-2 text-xs text-foreground/90">
                          {shot.description}
                        </p>
                        {shot.dialogue && (
                          <p className="line-clamp-2 text-[11px] italic text-muted-foreground">
                            {shot.dialogue}
                          </p>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {stage === "timeline" && (
        <Card>
          <CardHeader>
            <CardTitle>Cut timeline</CardTitle>
            <CardDescription>Linear assembly of every shot in runtime order.</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-2">
              {project.scenes.flatMap((scene) =>
                scene.shots.map((shot, idx) => (
                  <div
                    key={shot.id}
                    className="flex items-stretch gap-3 rounded-lg border border-border bg-surface/50 p-2"
                  >
                    <div className="w-28 shrink-0 overflow-hidden rounded-md bg-bg">
                      {shot.frameDataUrl ? (
                        <img
                          src={shot.frameDataUrl}
                          alt=""
                          className="aspect-video h-full w-full object-cover"
                        />
                      ) : (
                        <div className="aspect-video bg-surface" />
                      )}
                    </div>
                    <div className="min-w-0 flex-1 py-1">
                      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                        S{scene.number} · Shot {idx + 1} · {shot.type}
                      </div>
                      <div className="truncate text-sm text-foreground">{shot.description}</div>
                      {shot.dialogue && (
                        <div className="truncate text-xs italic text-muted-foreground">
                          {shot.dialogue}
                        </div>
                      )}
                    </div>
                    <div className="flex w-14 shrink-0 items-center justify-end pr-1 font-mono text-xs tabular-nums text-muted-foreground">
                      {shot.durationSec.toFixed(1)}s
                    </div>
                  </div>
                )),
              )}
            </div>
            <div className="mt-4 flex items-center justify-between border-t border-border pt-4 text-sm">
              <span className="text-muted-foreground">Total runtime</span>
              <span className="font-mono tabular-nums text-foreground">
                {formatDuration(runtime)} ({runtime.toFixed(1)}s)
              </span>
            </div>
          </CardContent>
        </Card>
      )}

      {stage === "render" && (
        <Card>
          <CardHeader className="flex-row items-start justify-between gap-3 space-y-0">
            <div>
              <CardTitle>Silver render</CardTitle>
              <CardDescription>
                Canvas + MediaRecorder produce a real WebM short you can play and download.
              </CardDescription>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                variant="silver"
                disabled={isWorking}
                onClick={async () => {
                  await renderOnly(project.id);
                  toast.success("Hero reel complete");
                }}
              >
                <Play className="h-4 w-4" />
                {isWorking ? "Rendering…" : "Render hero reel"}
              </Button>
              <Button
                variant="outline"
                disabled={isWorking}
                onClick={async () => {
                  await renderAllChapters(project.id);
                  toast.success("Chapter reels complete — full-length package");
                }}
              >
                <Layers className="h-4 w-4" />
                Render all chapters
              </Button>
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="letterbox-frame rounded-xl border border-border">
              {project.renderUrl ? (
                <video
                  key={project.renderUrl}
                  src={project.renderUrl}
                  controls
                  playsInline
                  className="h-full w-full object-contain"
                />
              ) : (
                <div className="flex h-full min-h-[220px] flex-col items-center justify-center gap-3 bg-gradient-to-b from-surface to-bg p-6 text-center">
                  <Film className="h-8 w-8 text-muted-foreground" strokeWidth={1.25} />
                  <p className="text-sm text-muted-foreground">
                    No render yet. Hit Render film to record the storyboard with Ken Burns motion
                    and dialogue captions.
                  </p>
                  {isWorking && (
                    <div className="h-1 w-48 overflow-hidden rounded-full bg-border">
                      <div className="h-full w-1/2 shimmer rounded-full bg-silver/40" />
                    </div>
                  )}
                </div>
              )}
            </div>
            {project.renderUrl && (
              <Button
                variant="outline"
                onClick={() =>
                  downloadUrl(
                    `${project.title.replace(/\s+/g, "_").toLowerCase()}.webm`,
                    project.renderUrl!,
                  )
                }
              >
                <Download className="h-4 w-4" />
                Download hero WebM
              </Button>
            )}
            {(project.chapters?.length ?? 0) > 0 && (
              <div className="space-y-2 border-t border-border pt-4">
                <div className="text-xs uppercase tracking-[0.14em] text-muted-foreground">
                  Full-length chapters · target {project.targetMinutes}m
                </div>
                <div className="grid gap-2 sm:grid-cols-2">
                  {project.chapters.map((ch) => (
                    <div
                      key={ch.number}
                      className="rounded-lg border border-border bg-surface/50 p-3"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <div className="text-sm font-medium text-foreground">
                          {ch.title}
                        </div>
                        <Badge variant="muted">Act {ch.act} · {ch.targetMinutes}m</Badge>
                      </div>
                      {ch.renderUrl ? (
                        <video
                          src={ch.renderUrl}
                          controls
                          playsInline
                          className="mt-2 aspect-video w-full rounded-md bg-bg"
                        />
                      ) : (
                        <p className="mt-2 text-xs text-muted-foreground">
                          Not rendered yet — use Render all chapters
                        </p>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {stage === "nft" && (
        <Card>
          <CardHeader className="flex-row items-start justify-between gap-3 space-y-0">
            <div>
              <CardTitle>NFT package</CardTitle>
              <CardDescription>
                OpenSea-ready metadata for your short film — traits, poster, and animation URL.
              </CardDescription>
            </div>
            <Button
              variant="silver"
              onClick={() => {
                packageNft(project.id);
                toast.success("NFT metadata sealed");
              }}
            >
              <Package className="h-4 w-4" />
              Seal package
            </Button>
          </CardHeader>
          <CardContent className="space-y-4">
            {project.nftMetadata ? (
              <>
                <div className="grid gap-4 sm:grid-cols-[160px_1fr]">
                  {project.posterDataUrl && (
                    <img
                      src={project.posterDataUrl}
                      alt="NFT art"
                      className="aspect-[2/3] w-full rounded-lg border border-border object-cover"
                    />
                  )}
                  <div className="space-y-3">
                    <div>
                      <div className="text-xs uppercase tracking-wider text-muted-foreground">
                        Name
                      </div>
                      <div className="font-display text-2xl">{project.nftMetadata.name}</div>
                    </div>
                    <p className="text-sm text-muted-foreground">
                      {project.nftMetadata.description}
                    </p>
                    <div className="flex flex-wrap gap-2">
                      {project.nftMetadata.attributes.map((a) => (
                        <Badge key={a.trait_type} variant="default">
                          {a.trait_type}: {String(a.value)}
                        </Badge>
                      ))}
                    </div>
                  </div>
                </div>
                <pre className="max-h-64 overflow-auto rounded-lg border border-border bg-surface p-4 font-mono text-xs text-muted-foreground">
                  {JSON.stringify(project.nftMetadata, null, 2)}
                </pre>
                <div className="flex flex-wrap gap-2">
                  <Button
                    variant="outline"
                    onClick={() =>
                      downloadJson(
                        `${project.title.replace(/\s+/g, "_").toLowerCase()}_metadata.json`,
                        project.nftMetadata,
                      )
                    }
                  >
                    <Download className="h-4 w-4" />
                    Download metadata JSON
                  </Button>
                  {project.renderUrl && (
                    <Button
                      variant="outline"
                      onClick={() =>
                        downloadUrl(
                          `${project.title.replace(/\s+/g, "_").toLowerCase()}.webm`,
                          project.renderUrl!,
                        )
                      }
                    >
                      <Download className="h-4 w-4" />
                      Download film asset
                    </Button>
                  )}
                </div>
              </>
            ) : (
              <p className="text-sm text-muted-foreground">
                Seal the package after rendering to attach traits and export files.
              </p>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
