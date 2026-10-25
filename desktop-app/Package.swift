// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "ArgoDesktop",
    platforms: [.macOS(.v14)],
    products: [.executable(name: "Argo", targets: ["Argo"])],
    targets: [.executableTarget(name: "Argo", path: "Sources/Argo")]
)
