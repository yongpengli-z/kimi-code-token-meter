import Cocoa
import Combine
import SwiftUI

struct ServerState: Decodable {
    let pid: Int
    let url: String
}

struct RequestDetail: Decodable, Identifiable {
    let time: Double
    let agentId: String?
    let model: String?
    let uncachedInputTokens: Int
    let cacheReadTokens: Int
    let inputTokens: Int
    let outputTokens: Int
    let speedTokensPerSecond: Double?
    let durationMs: Double?

    var id: String {
        return "\(time)-\(agentId ?? "")-\(outputTokens)-\(inputTokens)"
    }
}

struct MetricsSnapshot: Decodable {
    let sessionId: String?
    let speedTokensPerSecond: Double?
    let cacheHitPercent: Double?
    let requestCount: Int
    let outputTokens: Int
    let generating: Bool
    let lastUsageTime: Double?
    let recentRequests: [RequestDetail]?
}

struct AppConfiguration {
    let homePath: String
    let scriptPath: String
    let expanded: Bool

    static func current() -> AppConfiguration {
        let arguments = CommandLine.arguments
        let environment = ProcessInfo.processInfo.environment
        let defaultHome = environment["KIMI_CODE_HOME"] ?? NSHomeDirectory() + "/.kimi-code"
        let home = value(after: "--home", in: arguments) ?? defaultHome
        let defaultScript = home + "/plugins/managed/kimi-code-token-meter/scripts/token_meter.py"
        let script = value(after: "--script", in: arguments) ?? defaultScript
        return AppConfiguration(homePath: home, scriptPath: script, expanded: arguments.contains("--details"))
    }

    private static func value(after flag: String, in arguments: [String]) -> String? {
        guard let index = arguments.firstIndex(of: flag), index + 1 < arguments.count else {
            return nil
        }
        return arguments[index + 1]
    }
}

enum Format {
    static let integer: NumberFormatter = {
        let formatter = NumberFormatter()
        formatter.numberStyle = .decimal
        return formatter
    }()

    static let time: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "zh_CN")
        formatter.dateFormat = "HH:mm:ss"
        return formatter
    }()

    static func number(_ value: Int) -> String {
        return integer.string(from: NSNumber(value: value)) ?? String(value)
    }

    static func speed(_ value: Double?) -> String {
        guard let value = value else { return "--" }
        return String(format: "%.1f tok/s", value)
    }

    static func percent(_ value: Double?) -> String {
        guard let value = value else { return "--" }
        return String(format: "%.1f%%", value)
    }

    static func duration(_ value: Double?) -> String {
        guard let value = value else { return "--" }
        return String(format: "%.1fs", value / 1000.0)
    }

    static func timestamp(_ value: Double) -> String {
        return time.string(from: Date(timeIntervalSince1970: value / 1000.0))
    }

    static func modelName(_ value: String?) -> String {
        return value?.split(separator: "/").last.map(String.init) ?? "--"
    }

    static func sessionName(_ value: String?) -> String {
        guard let value = value, !value.isEmpty else { return "未发现会话" }
        return "会话 …" + String(value.suffix(10))
    }
}

final class MetricsModel: ObservableObject {
    @Published var snapshot: MetricsSnapshot?
    @Published var statusText = "正在连接本地服务…"
    @Published var isExpanded: Bool
    @Published var launchAtLogin: Bool

    let homePath: String
    let scriptPath: String
    private var timer: Timer?
    private var currentTask: URLSessionDataTask?
    private var inFlight = false
    private var startingServer = false
    private var lastStartAttempt = Date.distantPast

    init(configuration: AppConfiguration) {
        self.homePath = configuration.homePath
        self.scriptPath = configuration.scriptPath
        self.isExpanded = configuration.expanded
        self.launchAtLogin = LaunchAgentManager.isEnabled()
    }

    func start() {
        guard timer == nil else { return }
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { [weak self] _ in
            self?.refresh()
        }
    }

    func stop() {
        timer?.invalidate()
        timer = nil
        currentTask?.cancel()
        currentTask = nil
    }

    func setLaunchAtLogin(_ enabled: Bool) {
        let executable = Bundle.main.executablePath ?? CommandLine.arguments[0]
        do {
            try LaunchAgentManager.setEnabled(enabled, executablePath: executable, homePath: homePath, scriptPath: scriptPath)
            launchAtLogin = enabled
            statusText = enabled ? "已开启登录时启动" : "已关闭登录时启动"
        } catch {
            statusText = "登录启动设置失败：\(error.localizedDescription)"
        }
    }

    private func metricsURL() -> URL? {
        let path = homePath + "/kimi-code-token-meter/server.json"
        guard let data = FileManager.default.contents(atPath: path),
              let state = try? JSONDecoder().decode(ServerState.self, from: data),
              state.url.hasPrefix("http://127.0.0.1:"),
              let url = URL(string: state.url + "api/metrics") else {
            return nil
        }
        return url
    }

    private func refresh() {
        guard !inFlight else { return }
        guard let url = metricsURL() else {
            statusText = "正在启动本地服务…"
            startServerIfNeeded()
            return
        }
        inFlight = true
        currentTask = URLSession.shared.dataTask(with: url) { [weak self] data, response, error in
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.inFlight = false
                if error != nil {
                    self.statusText = "本地服务连接失败，正在重试"
                    self.startServerIfNeeded()
                    return
                }
                guard let http = response as? HTTPURLResponse, http.statusCode == 200,
                      let data = data,
                      let snapshot = try? JSONDecoder().decode(MetricsSnapshot.self, from: data) else {
                    self.statusText = "本地服务响应异常，正在重试"
                    self.startServerIfNeeded()
                    return
                }
                self.snapshot = snapshot
                if snapshot.generating {
                    self.statusText = "模型生成中 · 显示最近完成请求"
                } else if snapshot.lastUsageTime != nil {
                    self.statusText = "已连接 · 每秒自动刷新"
                } else {
                    self.statusText = "已连接 · 等待用量记录"
                }
            }
        }
        currentTask?.resume()
    }

    private func startServerIfNeeded() {
        guard !startingServer, Date().timeIntervalSince(lastStartAttempt) >= 10 else { return }
        startingServer = true
        lastStartAttempt = Date()
        let scriptPath = self.scriptPath
        let homePath = self.homePath
        DispatchQueue.global().async {
            var failure = ""
            for python in ["python3", "python"] {
                do {
                    let result = try Self.runPython(python: python, scriptPath: scriptPath, homePath: homePath)
                    if result.status == 0 {
                        DispatchQueue.main.async {
                            self.startingServer = false
                            self.refresh()
                        }
                        return
                    }
                    failure = result.output
                } catch {
                    failure = error.localizedDescription
                }
            }
            DispatchQueue.main.async {
                self.startingServer = false
                self.statusText = "本地服务启动失败：" + String(failure.suffix(160))
            }
        }
    }

    private static func runPython(python: String, scriptPath: String, homePath: String) throws -> (status: Int32, output: String) {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = [python, scriptPath, "--launch"]
        var environment = ProcessInfo.processInfo.environment
        environment["KIMI_CODE_HOME"] = homePath
        process.environment = environment
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        try process.run()
        process.waitUntilExit()
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        let output = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return (process.terminationStatus, output)
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var window: NSPanel?
    private var model: MetricsModel?
    private var cancellables = Set<AnyCancellable>()

    func applicationDidFinishLaunching(_ notification: Notification) {
        let model = MetricsModel(configuration: .current())
        self.model = model
        let panel = NSPanel(
            contentRect: NSRect(x: 0, y: 0, width: 360, height: 170),
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        panel.title = "Token Meter"
        panel.titleVisibility = .hidden
        panel.titlebarAppearsTransparent = true
        panel.standardWindowButton(.closeButton)?.isHidden = true
        panel.standardWindowButton(.miniaturizeButton)?.isHidden = true
        panel.standardWindowButton(.zoomButton)?.isHidden = true
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.isMovableByWindowBackground = true
        panel.contentView = NSHostingView(rootView: FloatingWindowView(model: model))
        panel.setFrameAutosaveName("TokenMeterFloatingWindow")
        if UserDefaults.standard.object(forKey: "NSWindow Frame TokenMeterFloatingWindow") == nil,
           let screen = NSScreen.main?.visibleFrame {
            panel.setFrameOrigin(NSPoint(x: screen.maxX - 380, y: screen.maxY - 190))
        }
        window = panel
        model.$isExpanded.sink { [weak self] _ in
            self?.resizeWindow(animated: true)
        }.store(in: &cancellables)
        resizeWindow(animated: false)
        panel.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        model.start()
    }

    func applicationWillTerminate(_ notification: Notification) {
        model?.stop()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        return true
    }

    private func resizeWindow(animated: Bool) {
        guard let window = window, let model = model else { return }
        let size = model.isExpanded ? NSSize(width: 1000, height: 640) : NSSize(width: 360, height: 170)
        var frame = window.frame
        let top = frame.origin.y + frame.height
        frame.origin.y = top - size.height
        frame.size = size
        let screen = NSScreen.screens.first(where: { $0.frame.contains(frame.origin) }) ?? NSScreen.main
        if let visible = screen?.visibleFrame {
            frame.size.width = min(frame.size.width, visible.width - 40)
            frame.size.height = min(frame.size.height, visible.height - 40)
            frame.origin.x = max(visible.minX + 12, min(frame.origin.x, visible.maxX - frame.width - 12))
            frame.origin.y = max(visible.minY + 12, min(frame.origin.y, visible.maxY - frame.height - 12))
        }
        window.setFrame(frame, display: true, animate: animated)
    }
}

struct FloatingWindowView: View {
    @ObservedObject var model: MetricsModel

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 16)
                .fill(Color(red: 0.055, green: 0.067, blue: 0.095).opacity(0.98))
            RoundedRectangle(cornerRadius: 16)
                .stroke(Color.white.opacity(0.12), lineWidth: 1)
            VStack(spacing: 12) {
                header
                HStack(spacing: 18) {
                    MetricView(title: "输出速度", value: Format.speed(model.snapshot?.speedTokensPerSecond), color: .green)
                    MetricView(title: "缓存命中率", value: Format.percent(model.snapshot?.cacheHitPercent), color: .blue)
                }
                status
                if model.isExpanded {
                    Divider().background(Color.white.opacity(0.12))
                    RequestDetailsView(records: model.snapshot?.recentRequests ?? [])
                    footer
                }
            }
            .padding(14)
        }
        .preferredColorScheme(.dark)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var header: some View {
        HStack(spacing: 8) {
            Circle()
                .fill(model.snapshot == nil ? Color.orange : Color.green)
                .frame(width: 7, height: 7)
            Text("Token Meter")
                .font(.system(size: 14, weight: .semibold))
            Spacer()
            Button(action: { model.isExpanded.toggle() }) {
                Image(systemName: model.isExpanded ? "chevron.down" : "chevron.up")
                    .font(.system(size: 12, weight: .semibold))
            }
            .buttonStyle(.plain)
            .help(model.isExpanded ? "收起明细" : "展开明细")
            Button(action: { NSApp.terminate(nil) }) {
                Image(systemName: "xmark")
                    .font(.system(size: 12, weight: .semibold))
            }
            .buttonStyle(.plain)
            .help("关闭悬浮窗")
        }
        .foregroundColor(.white)
    }

    private var status: some View {
        HStack(spacing: 8) {
            Text(model.statusText)
                .lineLimit(1)
                .truncationMode(.tail)
            Spacer()
            Text(Format.sessionName(model.snapshot?.sessionId))
                .foregroundColor(.secondary)
                .lineLimit(1)
        }
        .font(.system(size: 10))
        .foregroundColor(.secondary)
    }

    private var footer: some View {
        HStack {
            Toggle("登录时启动", isOn: Binding(get: { model.launchAtLogin }, set: { model.setLaunchAtLogin($0) }))
                .font(.system(size: 11))
                .foregroundColor(.secondary)
            Spacer()
            Text("仅本机数据 · 每秒刷新")
                .font(.system(size: 10))
                .foregroundColor(.secondary)
        }
    }
}

struct MetricView: View {
    let title: String
    let value: String
    let color: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(title)
                .font(.system(size: 10))
                .foregroundColor(.secondary)
            Text(value)
                .font(.system(size: 24, weight: .semibold, design: .rounded))
                .foregroundColor(color)
                .lineLimit(1)
                .minimumScaleFactor(0.75)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct RequestDetailsView: View {
    let records: [RequestDetail]
    private let widths: [CGFloat] = [68, 68, 105, 100, 100, 75, 92, 76]

    var body: some View {
        VStack(spacing: 0) {
            DetailRow(values: ["时间", "Agent", "模型", "未缓存输入", "缓存读取", "输出", "速度", "总耗时"], widths: widths)
                .font(.system(size: 10, weight: .medium))
                .foregroundColor(.secondary)
                .padding(.vertical, 8)
                .background(Color.white.opacity(0.035))
            if records.isEmpty {
                Text("暂无请求明细")
                    .font(.system(size: 12))
                    .foregroundColor(.secondary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    LazyVStack(spacing: 0) {
                        ForEach(Array(records.enumerated()), id: \.element.id) { index, record in
                            DetailRow(values: [
                                Format.timestamp(record.time),
                                record.agentId ?? "--",
                                Format.modelName(record.model),
                                Format.number(record.uncachedInputTokens),
                                Format.number(record.cacheReadTokens),
                                Format.number(record.outputTokens),
                                Format.speed(record.speedTokensPerSecond),
                                Format.duration(record.durationMs),
                            ], widths: widths)
                            .font(.system(size: 10, design: .monospaced))
                            .foregroundColor(.primary)
                            .padding(.vertical, 7)
                            .background(index % 2 == 0 ? Color.clear : Color.white.opacity(0.025))
                        }
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color.black.opacity(0.14))
        .cornerRadius(9)
    }
}

struct DetailRow: View {
    let values: [String]
    let widths: [CGFloat]

    var body: some View {
        HStack(spacing: 8) {
            ForEach(values.indices, id: \.self) { index in
                Text(values[index])
                    .lineLimit(1)
                    .truncationMode(.tail)
                    .frame(width: widths[index], alignment: index < 3 ? .leading : .trailing)
            }
        }
        .padding(.horizontal, 10)
    }
}
