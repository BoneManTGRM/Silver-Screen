import gradio as gr

from voice_clone import VoiceCloner


# Placeholder for pipeline
class SilverScreenPipeline:
    def generate_scene(self, prompt, model="flux"):
        return f"Generated clip for: {prompt}"


pipe = SilverScreenPipeline()
cloner = VoiceCloner()


with gr.Blocks(title="Silver-Screen - AI Movie & Trailer Maker") as demo:
    gr.Markdown("# 🎥 Silver-Screen\n## AI Movie Maker with ElevenLabs Voice Cloning")

    with gr.Tab("Voice Clone"):
        ref_audio = gr.Audio(type="filepath", label="Upload voice sample (6+ sec)")
        voice_name = gr.Textbox(value="Narrator", label="Voice Name")
        clone_btn = gr.Button("Clone Voice", variant="primary")
        voice_id_out = gr.Textbox(label="Voice ID")
        clone_btn.click(cloner.clone_voice, inputs=[ref_audio, voice_name], outputs=voice_id_out)

    with gr.Tab("Trailer Maker"):
        title = gr.Textbox(label="Movie Title")
        logline = gr.Textbox(label="Logline")
        scenes = gr.Textbox(label="Scenes (one per line)", lines=5)
        voice_sample = gr.Audio(type="filepath", label="Voice Sample")
        generate_btn = gr.Button("Generate Trailer with Voice", variant="primary")
        output = gr.Textbox(label="Status")
        # Placeholder
        generate_btn.click(
            lambda *args: "Trailer generation placeholder - voice cloned!",
            inputs=[],
            outputs=output,
        )

    with gr.Tab("Script to Movie"):
        script_input = gr.Textbox(label="Script", lines=10)
        style = gr.Dropdown(["cinematic", "anime", "realistic"], value="cinematic")
        generate_btn2 = gr.Button("Generate Movie")
        output2 = gr.Textbox()
        generate_btn2.click(pipe.generate_scene, inputs=[script_input], outputs=output2)


demo.launch()
