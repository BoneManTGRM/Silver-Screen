# Blueprint Runtime and API Setup

## Why an 8-second film could appear after choosing a 2-minute blueprint

The story format and the video render target were previously separate controls. A Trailer described a 2-minute story blueprint, while the video control still defaulted to 8 seconds. The Full Blueprint Production page removes that ambiguity.

With an 8-second provider clip duration:

| Blueprint | Runtime | Planned clips |
| --- | ---: | ---: |
| Trailer | 2 minutes | 15 |
| Short | 12 minutes | 90 |
| Episode | 24 minutes | 180 |
| Featurette | 45 minutes | 338 |
| Feature | 90 minutes | 675 |

The complete production is planned immediately, but checkpoint mode generates only the selected number of new clips in each browser request. A 2-minute trailer with a one-clip checkpoint should therefore report `1/15`, not `1/1 complete`.

## Current API requirements

### Required for generated video

```text
REPLICATE_API_TOKEN
```

Optional video settings:

```text
SILVER_SCREEN_VIDEO_MODEL=google/veo-3.1-fast
SILVER_SCREEN_VIDEO_DURATION=8
SILVER_SCREEN_VIDEO_RESOLUTION=720p
SILVER_SCREEN_VIDEO_ASPECT_RATIO=16:9
SILVER_SCREEN_VIDEO_AUDIO=1
```

### Choose one speech option

OpenAI speech:

```text
OPENAI_API_KEY
SILVER_SCREEN_OPENAI_TTS_MODEL=gpt-4o-mini-tts
```

Or ElevenLabs speech:

```text
ELEVENLABS_API_KEY
SILVER_SCREEN_ELEVENLABS_MODEL=eleven_multilingual_v2
```

Or upload authorized finished audio tracks, which requires no speech-provider API.

You do not need both OpenAI and ElevenLabs. Script parsing, runtime analysis, TGRM, subtitles, FFmpeg mixing, checkpointing, and artifact assembly are local application functions and do not require separate APIs.

## Safe long-render workflow

1. Open **Full Blueprint Production** from the Streamlit page navigation.
2. Select **Match the blueprint**.
3. Keep **New clips per checkpoint** at 1 or 2 on hosted Streamlit.
4. Keep continuous mode off unless a long-running deployment and explicit call ceiling are configured.
5. Start the production, then continue the same saved checkpoint until the planned clip count is complete.
6. Add voices or run Professional Script Sync after enough verified video exists.
