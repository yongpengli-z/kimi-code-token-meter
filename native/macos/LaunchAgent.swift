import Foundation

enum LaunchAgentManager {
    static let label = "com.github.yongpengli-z.kimi-code-token-meter"

    static func directory() -> URL {
        let environment = ProcessInfo.processInfo.environment
        if let override = environment["TOKEN_METER_LAUNCH_AGENTS_DIR"], !override.isEmpty {
            return URL(fileURLWithPath: override, isDirectory: true)
        }
        return URL(fileURLWithPath: NSHomeDirectory(), isDirectory: true)
            .appendingPathComponent("Library/LaunchAgents", isDirectory: true)
    }

    static func plistPath() -> URL {
        return directory().appendingPathComponent(label + ".plist")
    }

    static func isEnabled() -> Bool {
        return FileManager.default.fileExists(atPath: plistPath().path)
    }

    static func setEnabled(_ enabled: Bool, executablePath: String, homePath: String, scriptPath: String) throws {
        let fileManager = FileManager.default
        let path = plistPath()
        if !enabled {
            if fileManager.fileExists(atPath: path.path) {
                try fileManager.removeItem(at: path)
            }
            return
        }
        try fileManager.createDirectory(at: directory(), withIntermediateDirectories: true)
        let logPath = homePath + "/kimi-code-token-meter/floating.log"
        let plist: [String: Any] = [
            "Label": label,
            "ProgramArguments": [executablePath, "--home", homePath, "--script", scriptPath],
            "RunAtLoad": true,
            "ProcessType": "Interactive",
            "StandardOutPath": logPath,
            "StandardErrorPath": logPath,
        ]
        let data = try PropertyListSerialization.data(fromPropertyList: plist, format: .xml, options: 0)
        try data.write(to: path, options: .atomic)
    }
}
