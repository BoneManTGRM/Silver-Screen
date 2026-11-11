import Foundation
import MediaGenerationKit
import SwiftUI
import UniformTypeIdentifiers

enum StudioMedium: String, CaseIterable, Identifiable, Hashable, Sendable {
    case image = "Image"
    case clip = "Clip"

    var id: String { rawValue }

    var model: String {
        switch self {
        case .image: "sd_v1.5_f16.ckpt"
        case .clip: "wan_v2.1_1.3b_480p_q8p.ckpt"
        }
    }
}

@MainActor
final class Studio: ObservableObject {
    @Published private(set) var readyModels: Set<StudioMedium> = []
    @Published private(set) var isWorking = false
    @Published private(set) var status = ""
    @Published private(set) var output: URL?
    @Published var errorMessage: String?

    private var task: Task<Void, Never>?
    private var operationID: UUID?
    private let environment = MediaGenerationEnvironment.default

    init() {
        if let latestName = UserDefaults.standard.string(forKey: "latest-render") {
            let candidate = URL.documentsDirectory.appending(path: "Renders").appending(path: latestName)
            if FileManager.default.fileExists(atPath: candidate.path) {
                output = candidate
            }
        }
        for medium in StudioMedium.allCases {
            let key = Self.readyKey(medium)
            if UserDefaults.standard.bool(forKey: key),
               (try? environment.inspectModel(medium.model, offline: true).isDownloaded) == true {
                readyModels.insert(medium)
            }
        }
    }

    func isReady(_ medium: StudioMedium) -> Bool {
        readyModels.contains(medium)
    }

    func download(_ medium: StudioMedium) {
        start("Checking model files…") { id in
            _ = try await self.environment.ensure(medium.model) { state in
                let message: String
                switch state {
                case .resolving:
                    message = "Checking model files…"
                case .verifying(_, let index, let total):
                    message = "Verifying file \(index) of \(total)…"
                case .downloading(_, let index, let total, let received, let expected):
                    if expected > 0 {
                        let percent = min(100, Int(Double(received) / Double(expected) * 100))
                        message = "Downloading file \(index) of \(total) · \(percent)%"
                    } else {
                        message = "Downloading file \(index) of \(total)…"
                    }
                }
                Task { @MainActor in self.update(message, for: id) }
            }
            try Task.checkCancellation()
            UserDefaults.standard.set(true, forKey: Self.readyKey(medium))
            self.readyModels.insert(medium)
        }
    }

    func generate(_ medium: StudioMedium, prompt: String) {
        guard isReady(medium), !prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return
        }
        start("Loading local model…") { id in
            let render = try await LocalRenderer.render(medium, prompt: prompt) { message in
                Task { @MainActor in self.update(message, for: id) }
            }
            if Task.isCancelled {
                try? FileManager.default.removeItem(at: render)
                throw CancellationError()
            }
            self.output = render
            UserDefaults.standard.set(render.lastPathComponent, forKey: "latest-render")
        }
    }

    func cancel() {
        task?.cancel()
        status = "Cancelling…"
    }

    private static func readyKey(_ medium: StudioMedium) -> String {
        "downloaded-model.\(medium.model)"
    }

    private func update(_ message: String, for id: UUID) {
        guard operationID == id, task?.isCancelled == false else { return }
        status = message
    }

    private func start(_ message: String, action: @escaping (UUID) async throws -> Void) {
        guard task == nil else { return }
        let id = UUID()
        operationID = id
        isWorking = true
        status = message
        task = Task {
            defer {
                operationID = nil
                task = nil
                isWorking = false
            }
            do {
                try await action(id)
            } catch is CancellationError {
                return
            } catch {
                errorMessage = error.localizedDescription
            }
        }
    }
}

enum LocalRenderer {
    static func render(
        _ medium: StudioMedium,
        prompt: String,
        report: @escaping @Sendable (String) -> Void
    ) async throws -> URL {
        var pipeline = try await MediaGenerationPipeline.fromPretrained(medium.model, backend: .local)
        pipeline.configuration.batchCount = 1
        pipeline.configuration.batchSize = 1
        switch medium {
        case .image:
            pipeline.configuration.width = 512
            pipeline.configuration.height = 512
            pipeline.configuration.steps = 20
        case .clip:
            pipeline.configuration.width = 512
            pipeline.configuration.height = 320
            pipeline.configuration.numFrames = 17
            pipeline.configuration.steps = 20
        }
        let frames = try await pipeline.generate(prompt: prompt, negativePrompt: "") { state in
            if case .generating(let step, let total) = state {
                report("Generating on this iPhone · \(step)/\(total)")
            }
        }
        try Task.checkCancellation()

        let renders = URL.documentsDirectory.appending(path: "Renders", directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: renders, withIntermediateDirectories: true)
        let base = renders.appending(path: UUID().uuidString)
        switch medium {
        case .image:
            guard let image = frames.first else { throw StudioError.emptyResult }
            let url = base.appendingPathExtension("png")
            try image.write(to: url, type: .png, options: .atomic)
            return url
        case .clip:
            guard frames.count >= 2 else { throw StudioError.emptyResult }
            report("Saving MP4…")
            let url = base.appendingPathExtension("mp4")
            try await ClipWriter.write(frames, to: url, framesPerSecond: 16)
            return url
        }
    }
}

enum StudioError: LocalizedError {
    case emptyResult
    case videoEncoding

    var errorDescription: String? {
        switch self {
        case .emptyResult: "The model did not return enough frames to save this render."
        case .videoEncoding: "Silver Screen could not encode the clip as MP4."
        }
    }
}
