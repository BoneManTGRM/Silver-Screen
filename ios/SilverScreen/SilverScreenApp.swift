import AVKit
import SwiftUI

@main
struct SilverScreenApp: App {
    var body: some Scene {
        WindowGroup {
            StudioView()
        }
    }
}

private struct StudioView: View {
    @StateObject private var studio = Studio()
    @State private var medium: StudioMedium = .image
    @State private var prompt = ""

    var body: some View {
        NavigationStack {
            Form {
                Section("Create on this iPhone") {
                    Picker("Output", selection: $medium) {
                        ForEach(StudioMedium.allCases) { option in
                            Text(option.rawValue).tag(option)
                        }
                    }
                    .pickerStyle(.segmented)
                    .disabled(studio.isWorking)

                    TextField("Describe your \(medium.rawValue.lowercased())", text: $prompt, axis: .vertical)
                        .lineLimit(4...8)
                        .disabled(studio.isWorking)

                    if studio.isReady(medium) {
                        Button("Generate \(medium.rawValue.lowercased())") {
                            studio.generate(medium, prompt: prompt)
                        }
                        .disabled(studio.isWorking || prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    } else {
                        Button("Download \(medium.rawValue.lowercased()) model") {
                            studio.download(medium)
                        }
                        .disabled(studio.isWorking)
                        Text("Download once over Wi-Fi. Generation uses the model stored on your phone.")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                }

                if studio.isWorking {
                    Section {
                        ProgressView(studio.status)
                        Button("Cancel", role: .cancel) {
                            studio.cancel()
                        }
                    }
                }

                if let output = studio.output {
                    Section("Latest render") {
                        if output.pathExtension == "mp4" {
                            ClipPreview(url: output)
                                .id(output)
                                .aspectRatio(1.6, contentMode: .fit)
                        } else if let image = UIImage(contentsOfFile: output.path) {
                            Image(uiImage: image).resizable().scaledToFit()
                        }
                        ShareLink(item: output) {
                            Label("Save or share", systemImage: "square.and.arrow.up")
                        }
                    }
                }
            }
            .navigationTitle("Silver Screen")
            .alert("Silver Screen", isPresented: Binding(
                get: { studio.errorMessage != nil },
                set: { if !$0 { studio.errorMessage = nil } }
            )) {
                Button("OK") { studio.errorMessage = nil }
            } message: {
                Text(studio.errorMessage ?? "")
            }
        }
    }
}

private struct ClipPreview: View {
    @State private var player: AVPlayer

    init(url: URL) {
        _player = State(initialValue: AVPlayer(url: url))
    }

    var body: some View {
        VideoPlayer(player: player)
            .onDisappear { player.pause() }
    }
}
