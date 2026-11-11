//@api-1.0

// Draw Things supplies the model, canvas, and generation settings.
function renderOffline() {
  const configuration = pipeline.configuration;
  if (!configuration.model || !pipeline.areModelsDownloaded([configuration.model])[0]) {
    throw new Error("Select a downloaded model in Draw Things before running Silver Screen Offline.");
  }

  const form = requestFromUser("Silver Screen Offline", "Generate", function () {
    return [
      this.section(
        "Image or clip",
        "Uses the downloaded model, starting image, and settings selected in Draw Things.",
        [
          this.textField("", "Describe the image or the motion you want", true, 130),
          this.textField("", "Optional negative prompt", true, 76),
        ],
      ),
    ];
  });

  if (!Array.isArray(form?.[0])) {
    return;
  }

  const [prompt, negativePrompt = ""] = form[0];
  if (typeof prompt !== "string" || !prompt.trim()) {
    throw new Error("Enter a prompt to generate an image or clip.");
  }

  pipeline.run({ configuration, prompt, negativePrompt });
}

renderOffline();
