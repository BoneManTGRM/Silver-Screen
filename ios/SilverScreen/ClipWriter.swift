import AVFoundation
import CoreGraphics
import CoreVideo
import Foundation
import ImageIO
import MediaGenerationKit
import UniformTypeIdentifiers

enum ClipWriter {
    static func write(
        _ frames: [MediaGenerationPipeline.Result],
        to destination: URL,
        framesPerSecond: Int32
    ) async throws {
        guard let first = frames.first, framesPerSecond > 0 else { throw StudioError.emptyResult }
        let width = first.width
        let height = first.height
        guard width > 0, height > 0 else { throw StudioError.videoEncoding }

        let scratch = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        try FileManager.default.createDirectory(at: scratch, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: scratch) }

        let videoURL = scratch.appending(path: "render.mp4")
        let frameURL = scratch.appending(path: "frame.png")
        let writer = try AVAssetWriter(outputURL: videoURL, fileType: .mp4)
        let input = AVAssetWriterInput(mediaType: .video, outputSettings: [
            AVVideoCodecKey: AVVideoCodecType.h264,
            AVVideoWidthKey: width,
            AVVideoHeightKey: height,
        ])
        input.expectsMediaDataInRealTime = false
        let adaptor = AVAssetWriterInputPixelBufferAdaptor(
            assetWriterInput: input,
            sourcePixelBufferAttributes: [
                kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA,
                kCVPixelBufferWidthKey as String: width,
                kCVPixelBufferHeightKey as String: height,
                kCVPixelBufferCGImageCompatibilityKey as String: true,
                kCVPixelBufferCGBitmapContextCompatibilityKey as String: true,
            ]
        )
        guard writer.canAdd(input) else { throw StudioError.videoEncoding }
        writer.add(input)
        guard writer.startWriting() else { throw writer.error ?? StudioError.videoEncoding }
        defer {
            if writer.status == .writing { writer.cancelWriting() }
        }
        writer.startSession(atSourceTime: .zero)
        guard let pool = adaptor.pixelBufferPool else { throw StudioError.videoEncoding }

        for (index, frame) in frames.enumerated() {
            try Task.checkCancellation()
            let clock = ContinuousClock()
            let deadline = clock.now.advanced(by: .seconds(15))
            while !input.isReadyForMoreMediaData {
                guard writer.status == .writing, clock.now < deadline else {
                    throw writer.error ?? StudioError.videoEncoding
                }
                try await Task.sleep(for: .milliseconds(20))
            }
            let appended = try autoreleasepool {
                try frame.write(to: frameURL, type: .png)
                guard let source = CGImageSourceCreateWithURL(frameURL as CFURL, nil),
                      let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
                    throw StudioError.videoEncoding
                }
                let buffer = try pixelBuffer(image, pool: pool, width: width, height: height)
                return adaptor.append(buffer, withPresentationTime: CMTime(
                    value: Int64(index), timescale: framesPerSecond
                ))
            }
            guard appended else { throw writer.error ?? StudioError.videoEncoding }
        }

        writer.endSession(atSourceTime: CMTime(value: Int64(frames.count), timescale: framesPerSecond))
        input.markAsFinished()
        await writer.finishWriting()
        try Task.checkCancellation()
        guard writer.status == .completed else { throw writer.error ?? StudioError.videoEncoding }
        try FileManager.default.moveItem(at: videoURL, to: destination)
    }

    private static func pixelBuffer(
        _ image: CGImage, pool: CVPixelBufferPool, width: Int, height: Int
    ) throws -> CVPixelBuffer {
        var buffer: CVPixelBuffer?
        guard CVPixelBufferPoolCreatePixelBuffer(nil, pool, &buffer) == kCVReturnSuccess,
              let buffer else { throw StudioError.videoEncoding }
        guard CVPixelBufferLockBaseAddress(buffer, []) == kCVReturnSuccess else {
            throw StudioError.videoEncoding
        }
        defer { CVPixelBufferUnlockBaseAddress(buffer, []) }
        let bitmapInfo = CGBitmapInfo.byteOrder32Little.rawValue | CGImageAlphaInfo.premultipliedFirst.rawValue
        guard let context = CGContext(
            data: CVPixelBufferGetBaseAddress(buffer), width: width, height: height,
            bitsPerComponent: 8, bytesPerRow: CVPixelBufferGetBytesPerRow(buffer),
            space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: bitmapInfo
        ) else { throw StudioError.videoEncoding }
        context.draw(image, in: CGRect(x: 0, y: 0, width: CGFloat(width), height: CGFloat(height)))
        return buffer
    }
}
