import gradio as gr
from voice_clone import VoiceCloner
from pipeline import SilverScreenPipeline

pipe = SilverScreenPipeline()
cloner = VoiceCloner()

with gr.Blocks(title='Silver-Screen - AI Movie Studio') as demo:
    gr.Markdown('# 🎥 Silver-Screen\n## AI Movie Maker')
    with gr.Tab('Voice Clone'):
        ref_audio = gr.Audio(type='filepath', label='Voice Sample')
        voice_name = gr.Textbox('Narrator', label='Voice Name')
        btn = gr.Button('Clone Voice')
        out = gr.Textbox()
        btn.click(cloner.clone_voice, inputs=[ref_audio, voice_name], outputs=out)
    with gr.Tab('Generate'):
        prompt = gr.Textbox('Movie prompt', label='Prompt')
        btn2 = gr.Button('Generate')
        out2 = gr.Textbox()
        btn2.click(pipe.generate_scene, inputs=[prompt], outputs=out2)
    gr.Markdown('App running. Add .env keys for full features.')
demo.launch()
