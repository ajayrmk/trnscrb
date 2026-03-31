/// sck-capture — ScreenCaptureKit audio capture helper for trnscrb.
///
/// Captures audio from a specific app (by bundle ID) and writes raw
/// float32 PCM at 16 kHz mono to stdout. Status/errors go to stderr.
///
/// Usage:
///   sck-capture <bundle-id>          Capture audio, write PCM to stdout
///   sck-capture --check              Exit 0 if Screen Recording permitted

import Foundation
import ScreenCaptureKit
import AVFoundation
import CoreMedia

// MARK: - Audio capture handler

@available(macOS 13.0, *)
class AudioCaptureHandler: NSObject, SCStreamDelegate, SCStreamOutput {
    private var stream: SCStream?
    private let bundleID: String
    private var isRunning = false

    init(bundleID: String) {
        self.bundleID = bundleID
        super.init()
    }

    func start() async throws {
        guard CGPreflightScreenCaptureAccess() else {
            fputs("ERROR: Screen Recording permission not granted\n", stderr)
            fputs("Enable in: System Settings → Privacy & Security → Screen Recording\n", stderr)
            throw NSError(domain: "sck-capture", code: 1,
                         userInfo: [NSLocalizedDescriptionKey: "Screen Recording permission required"])
        }

        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)

        guard let app = content.applications.first(where: { $0.bundleIdentifier == bundleID }) else {
            fputs("ERROR: App '\(bundleID)' not found\n", stderr)
            throw NSError(domain: "sck-capture", code: 2,
                         userInfo: [NSLocalizedDescriptionKey: "Application not found"])
        }

        guard let display = content.displays.first else {
            fputs("ERROR: No display found\n", stderr)
            throw NSError(domain: "sck-capture", code: 3,
                         userInfo: [NSLocalizedDescriptionKey: "No display"])
        }

        let config = SCStreamConfiguration()
        config.capturesAudio = true
        config.sampleRate = 16000
        config.channelCount = 1
        config.excludesCurrentProcessAudio = true

        // Video is required by SCK but we minimize it
        config.width = 2
        config.height = 2
        config.minimumFrameInterval = CMTime(value: 1, timescale: 1)

        let filter = SCContentFilter(display: display, including: [app], exceptingWindows: [])
        let newStream = SCStream(filter: filter, configuration: config, delegate: self)
        try newStream.addStreamOutput(self, type: .audio, sampleHandlerQueue: .global(qos: .userInteractive))

        try await newStream.startCapture()
        stream = newStream
        isRunning = true
        fputs("READY\n", stderr)
    }

    func stop() async {
        guard isRunning else { return }
        try? await stream?.stopCapture()
        isRunning = false
    }

    // MARK: - SCStreamOutput

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio else { return }
        guard let block = CMSampleBufferGetDataBuffer(sampleBuffer) else { return }

        var length = 0
        var ptr: UnsafeMutablePointer<Int8>?
        let status = CMBlockBufferGetDataPointer(block, atOffset: 0, lengthAtOffsetOut: nil, totalLengthOut: &length, dataPointerOut: &ptr)
        guard status == kCMBlockBufferNoErr, let data = ptr else { return }

        FileHandle.standardOutput.write(Data(bytes: data, count: length))
    }

    // MARK: - SCStreamDelegate

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        fputs("ERROR: Stream stopped: \(error.localizedDescription)\n", stderr)
        isRunning = false
    }
}

// MARK: - Main

@available(macOS 13.0, *)
@main
struct SCKCapture {
    static func main() async {
        let args = CommandLine.arguments

        // --check mode: test Screen Recording permission
        if args.count >= 2 && args[1] == "--check" {
            if CGPreflightScreenCaptureAccess() {
                fputs("Screen Recording: permitted\n", stderr)
                exit(0)
            } else {
                fputs("Screen Recording: denied\n", stderr)
                fputs("Enable in: System Settings → Privacy & Security → Screen Recording\n", stderr)
                exit(1)
            }
        }

        guard args.count >= 2 else {
            fputs("Usage: sck-capture <bundle-id>\n", stderr)
            fputs("       sck-capture --check\n", stderr)
            exit(1)
        }

        let bundleID = args[1]
        let handler = AudioCaptureHandler(bundleID: bundleID)

        // Handle SIGTERM for clean shutdown
        let sigSource = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
        signal(SIGTERM, SIG_IGN)
        sigSource.setEventHandler {
            Task {
                await handler.stop()
                exit(0)
            }
        }
        sigSource.resume()

        // Handle SIGINT (Ctrl+C) too
        let intSource = DispatchSource.makeSignalSource(signal: SIGINT, queue: .main)
        signal(SIGINT, SIG_IGN)
        intSource.setEventHandler {
            Task {
                await handler.stop()
                exit(0)
            }
        }
        intSource.resume()

        do {
            try await handler.start()
            // Run until signalled
            while true {
                try await Task.sleep(nanoseconds: 1_000_000_000)
            }
        } catch {
            fputs("FATAL: \(error.localizedDescription)\n", stderr)
            exit(1)
        }
    }
}
