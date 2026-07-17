import gradio as gr
import os
from voice_clone import VoiceCloner
from pipeline import SilverScreenPipeline

cloner = VoiceCloner()
pipe = SilverScreenPipeline()

with gr.Blocks(title="🎬 Silver-Screen AI Movie Studio - Maxed Out", theme=gr.themes.Dark()) as demo:
    gr.Markdown("""
    # 🎥 Silver-Screen AI Movie Studio
    ## Full AI Film Maker: Script → Scenes → Voice → Video → Edit → Export (NFT ready)
    """)

    with gr.Row():
        with gr.Column():
            gr.Markdown("### 🚀 Quick Start")
            api_key_status = gr.Textbox(value="ElevenLabs ready (placeholder)", label="Status")

    with gr.Tabs():
        with gr.Tab("1. Script Generator"):
            topic = gr.Textbox(label="Movie Concept / Topic", placeholder="A cyberpunk heist in 2049 with AI companions")
            length = gr.Slider(5, 120, value=30, label="Movie length (minutes)")
            genre = gr.Dropdown(["Sci-Fi", "Fantasy", "Action", "Horror", "Comedy"], label="Genre")
            generate_script_btn = gr.Button("Generate Full Script", variant="primary")
            script_output = gr.Textbox(label="Generated Script", lines=15)
            generate_script_btn.click(lambda t,g,l: f"[SIMULATED] Full script for {t} in {g} style ({l}min). 42 scenes ready.", inputs=[topic, genre, length], outputs=script_output)

        with gr.Tab("2. Voice Cloning & Narration"):
            ref_audio = gr.Audio(type="filepath", label="Reference Voice Audio (6+ seconds)")
            voice_name = gr.Textbox(value="Main Narrator", label="Voice Name")
            clone_btn = gr.Button("Clone Voice", variant="primary")
            voice_id = gr.Textbox(label="Cloned Voice ID")
            clone_btn.click(cloner.clone_voice, inputs=[ref_audio, voice_name], outputs=voice_id)

            with gr.Row():
                text_to_speak = gr.Textbox(label="Text to Narrate", lines=3)
                speak_btn = gr.Button("Generate Narration Audio")
                narration_out = gr.Audio(label="Narration Output")
                speak_btn.click(lambda v, t: (None, "Voice narration generated"), inputs=[voice_id, text_to_speak], outputs=[narration_out])

        with gr.Tab("3. Scene Visuals"):
            scenes_input = gr.Textbox(label="Scenes (one per line)", lines=8, value="Scene 1: Opening shot...\nScene 2: ...")
            style = gr.Dropdown(["Cinematic", "Anime", "Realistic", "Surreal"], value="Cinematic", label="Visual Style")
            generate_visuals_btn = gr.Button("Generate Scene Images/Video Clips", variant="primary")
            gallery = gr.Gallery(label="Generated Visuals")
            generate_visuals_btn.click(lambda s, st: [f"clip_{i}.mp4" for i in range(3)], inputs=[scenes_input, style], outputs=gallery)

        with gr.Tab("4. Full Movie Assembly"):
            assemble_btn = gr.Button("Assemble Full Movie + Audio Sync", variant="primary", size="large")
            final_video = gr.Video(label="Final Movie Preview")
            status = gr.Textbox(label="Assembly Status")
            assemble_btn.click(pipe.generate_full_movie, inputs=[], outputs=[final_video, status])

        with gr.Tab("5. Export & NFT"):
            export_btn = gr.Button("Export MP4 + NFT Metadata", variant="primary")
            download = gr.File(label="Download Movie")
            nft_info = gr.JSON(label="NFT Metadata")
            export_btn.click(lambda: ("final_movie.mp4", {"name": "MyAI Movie #001", "ipfs": "Qm..."}), outputs=[download, nft_info])

    gr.Markdown("---\n**Pro tips:** Add your API keys in `.env` for real ElevenLabs + ComfyUI/Replicate generation.")

demo.queue().launch(share=True)
