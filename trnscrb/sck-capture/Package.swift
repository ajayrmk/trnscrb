// swift-tools-version: 5.9

import PackageDescription

let package = Package(
    name: "sck-capture",
    platforms: [
        .macOS(.v13)
    ],
    targets: [
        .executableTarget(
            name: "sck-capture",
            linkerSettings: [
                .linkedFramework("ScreenCaptureKit"),
                .linkedFramework("AVFoundation"),
                .linkedFramework("CoreMedia"),
            ]
        ),
    ]
)
