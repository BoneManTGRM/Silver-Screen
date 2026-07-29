# Complete Local Setup for Silver-Screen Movie Maker (Personal Use Only)

## Goal: Fully offline/local AI movie production for your 80-min NFT film.

1. **Install ComfyUI** (best for video consistency and quality):
   - Clone ComfyUI repo
   - Install custom nodes for video (AnimateDiff, SVD, ControlNet, IP-Adapter, FaceID)
   - Download models from HF (Flux, SDXL, etc.)

2. **Ollama for LLM script writing**:
   - Install Ollama, pull llama3 or better model
   - Use in pipeline for scene breakdown.

3. **FFmpeg & MoviePy** for editing.

4. **Run the app**:
   - python app.py

**Advantages**:
- No API costs
- Unlimited generations
- Full control over consistency (LoRAs for NFT characters)
- High quality with proper workflows

See COMFYUI_SETUP.md for detailed ComfyUI video workflow.

This makes Silver-Screen a complete personal movie studio.