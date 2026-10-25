import AppKit
import Combine
import Foundation
import SwiftUI

enum ArgoVoiceState: String {
    case idle
    case listening
    case capturingCommand = "capturing_command"
    case thinking
    case speaking
    case awaitingAnswer = "awaiting_answer"
    case stopping

    var title: String {
        switch self {
        case .idle: "Ready when you are"
        case .listening: "Listening"
        case .capturingCommand: "Go ahead, I’m listening"
        case .thinking: "Thinking it through"
        case .speaking: "Argo is talking"
        case .awaitingAnswer: "Your turn"
        case .stopping: "Stopping Argo…"
        }
    }

    var subtitle: String {
        switch self {
        case .idle: "Start listening, then talk to Argo naturally."
        case .listening: "Say “Hey Argo” to start a request."
        case .capturingCommand: "Finish your request. Argo will start when you pause."
        case .thinking: "Working on your Jira request…"
        case .speaking: "Listen to Argo’s response."
        case .awaitingAnswer: "Argo has a question. Answer directly—no wake phrase needed."
        case .stopping: "Closing the microphone and audio session."
        }
    }

    var symbol: String {
        switch self {
        case .idle: "waveform"
        case .listening: "waveform"
        case .capturingCommand: "waveform.circle.fill"
        case .thinking: "sparkles"
        case .speaking: "waveform"
        case .awaitingAnswer: "ellipsis.bubble.fill"
        case .stopping: "stop.circle.fill"
        }
    }

    var color: Color {
        switch self {
        case .idle: .gray
        case .listening: .blue
        case .capturingCommand: .cyan
        case .thinking: .orange
        case .speaking: .purple
        case .awaitingAnswer: .teal
        case .stopping: .orange
        }
    }

    var animates: Bool { self == .listening || self == .capturingCommand || self == .speaking || self == .awaitingAnswer }
}

@main
struct ArgoDesktopApp: App {
    @StateObject private var model = ArgoModel()

    var body: some Scene {
        WindowGroup {
            ArgoRootView()
                .environmentObject(model)
                .frame(minWidth: 640, minHeight: 480)
                .preferredColorScheme(.light)
        }
        .windowStyle(.hiddenTitleBar)
        .defaultSize(width: 680, height: 500)
    }
}

@MainActor
final class ArgoModel: ObservableObject {
    @Published var config = ArgoConfig.load()
    @Published var credentials = ArgoCredentials.load()
    @Published var cartesiaInput = ""
    @Published var anthropicInput = ""
    @Published var jiraTokenInput = ""
    @Published var argoApiKeyInput = ""
    @Published var logs: [String] = []
    @Published var projects: [JiraProject] = []
    @Published var projectSearch = ""
    @Published var projectLoadError: String?
    @Published var isLoadingProjects = false
    @Published var isShowingProjectPicker = false
    @Published var isRunning = false
    @Published var voiceState: ArgoVoiceState = .idle
    @Published var errorMessage: String?
    @Published var infoMessage: String?
    @Published var selectedPage: AppPage = .home

    private var agentProcess: Process?
    private var outputPipe: Pipe?
    private var didMigrateDotEnv = false
    private var stateMarkerCarry = ""

    init() {
        migrateExistingDotEnv()
        refreshProcessState()
        if isConfigured { Task { await loadProjects() } }
    }

    var hasCartesiaKey: Bool { !credentials.cartesiaAPIKey.isEmpty }
    var hasAnthropicKey: Bool { !credentials.anthropicAPIKey.isEmpty }
    var hasJiraToken: Bool { !credentials.jiraAPIToken.isEmpty }
    var hasArgoApiKey: Bool { !credentials.argoAPIKey.isEmpty }

    var isConfigured: Bool {
        hasCartesiaKey && hasAnthropicKey && hasJiraToken &&
            !config.jiraEmail.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty &&
            !config.jiraBaseURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    func saveCredentials() {
        do {
            if !cartesiaInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                credentials.cartesiaAPIKey = cartesiaInput.trimmingCharacters(in: .whitespacesAndNewlines)
            }
            if !anthropicInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                credentials.anthropicAPIKey = anthropicInput.trimmingCharacters(in: .whitespacesAndNewlines)
            }
            if !jiraTokenInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                credentials.jiraAPIToken = jiraTokenInput.trimmingCharacters(in: .whitespacesAndNewlines)
            }
            config.jiraBaseURL = config.jiraBaseURL.trimmingCharacters(in: .whitespacesAndNewlines)
            config.jiraEmail = config.jiraEmail.trimmingCharacters(in: .whitespacesAndNewlines)
            config.save()
            try credentials.save()
            cartesiaInput = ""
            anthropicInput = ""
            jiraTokenInput = ""
            argoApiKeyInput = ""
            errorMessage = nil
            infoMessage = "Your connections are saved on this Mac."
            Task { await loadProjects() }
        } catch {
            errorMessage = "Could not save credentials: \(error.localizedDescription)"
        }
    }

    func saveDeveloperSettings() {
        config.projectKey = config.projectKey.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        config.audioDevice = config.audioDevice.trimmingCharacters(in: .whitespacesAndNewlines)
        if config.projectKey.isEmpty { config.projectKey = "ARGO" }
        if config.audioDevice.isEmpty { config.audioDevice = "default" }
        config.save()
        do {
            if !argoApiKeyInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                credentials.argoAPIKey = argoApiKeyInput.trimmingCharacters(in: .whitespacesAndNewlines)
            }
            try credentials.save()
            argoApiKeyInput = ""
            infoMessage = "Settings saved."
            errorMessage = nil
        } catch {
            errorMessage = "Could not save settings: \(error.localizedDescription)"
        }
    }

    func startAgent() {
        guard isConfigured else {
            errorMessage = "Finish connecting Cartesia, Claude, and Jira before starting Argo."
            selectedPage = .devInfo
            return
        }
        guard agentProcess?.isRunning != true else { return }
        config.save()

        guard let agentDirectory = locateAgentDirectory() else {
            errorMessage = "Argo's agent files could not be found. Rebuild the app from the Argo folder."
            return
        }
        guard let pythonURL = locatePython(agentDirectory: agentDirectory) else {
            errorMessage = "Python could not be found. Install the agent requirements, then rebuild Argo."
            return
        }

        let process = Process()
        process.executableURL = pythonURL
        process.arguments = [agentDirectory.appendingPathComponent("cartesia_stream.py").path,
                             "--device", config.audioDevice]
        process.currentDirectoryURL = agentDirectory
        process.environment = makeEnvironment()

        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        outputPipe = pipe
        pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty, let line = String(data: data, encoding: .utf8) else { return }
            DispatchQueue.main.async { self?.appendOutput(line) }
        }
        process.terminationHandler = { [weak self] process in
            DispatchQueue.main.async {
                self?.isRunning = false
                self?.voiceState = .idle
                self?.appendOutput("\n[desktop] Argo stopped (exit \(process.terminationStatus))\n")
            }
        }

        do {
            try process.run()
            agentProcess = process
            isRunning = true
            voiceState = .thinking
            logs = []
            appendOutput("[desktop] Argo is starting\n")
            errorMessage = nil
            infoMessage = nil
        } catch {
            errorMessage = "Argo couldn't start: \(error.localizedDescription)"
        }
    }

    func stopAgent() {
        guard let process = agentProcess, process.isRunning else {
            isRunning = false
            voiceState = .idle
            return
        }
        guard voiceState != .stopping else { return }
        voiceState = .stopping
        process.interrupt()
        appendOutput("[desktop] Stop requested\n")
    }

    func clearMessages() {
        errorMessage = nil
        infoMessage = nil
    }

    var currentProjectName: String {
        projects.first(where: { $0.key == config.projectKey })?.name ?? config.projectKey
    }

    var filteredProjects: [JiraProject] {
        let query = projectSearch.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty else { return projects }
        return projects.filter {
            $0.name.localizedCaseInsensitiveContains(query) || $0.key.localizedCaseInsensitiveContains(query)
        }
    }

    func loadProjects() async {
        guard !credentials.jiraAPIToken.isEmpty, !config.jiraEmail.isEmpty,
              let baseURL = URL(string: config.jiraBaseURL.trimmingCharacters(in: .whitespacesAndNewlines).trimmingCharacters(in: CharacterSet(charactersIn: "/"))) else {
            projectLoadError = "Connect your Jira account to load projects."
            return
        }

        isLoadingProjects = true
        projectLoadError = nil
        defer { isLoadingProjects = false }

        do {
            var found: [JiraProject] = []
            var startAt = 0
            var hasMore = true
            while hasMore {
                let url = baseURL.appendingPathComponent("rest/api/3/project/search")
                var components = URLComponents(url: url, resolvingAgainstBaseURL: false)!
                components.queryItems = [
                    URLQueryItem(name: "startAt", value: String(startAt)),
                    URLQueryItem(name: "maxResults", value: "100"),
                    URLQueryItem(name: "orderBy", value: "name"),
                ]
                var request = URLRequest(url: components.url!)
                let auth = Data("\(config.jiraEmail):\(credentials.jiraAPIToken)".utf8).base64EncodedString()
                request.setValue("Basic \(auth)", forHTTPHeaderField: "Authorization")
                request.setValue("application/json", forHTTPHeaderField: "Accept")
                let (data, response) = try await URLSession.shared.data(for: request)
                guard let http = response as? HTTPURLResponse else {
                    throw ProjectLoadError("Jira returned an invalid response.")
                }
                guard (200..<300).contains(http.statusCode) else {
                    let explanation = http.statusCode == 401 || http.statusCode == 403
                        ? "Jira couldn't authorize this account. Check the Jira connection in Dev info."
                        : "Jira couldn't load projects (HTTP \(http.statusCode))."
                    throw ProjectLoadError(explanation)
                }
                let payload = try JSONSerialization.jsonObject(with: data) as? [String: Any] ?? [:]
                let values = payload["values"] as? [[String: Any]] ?? []
                found.append(contentsOf: values.compactMap { item in
                    guard let key = item["key"] as? String, let name = item["name"] as? String else { return nil }
                    return JiraProject(key: key, name: name)
                })
                startAt = (payload["startAt"] as? Int ?? startAt) + values.count
                hasMore = !(payload["isLast"] as? Bool ?? true) && !values.isEmpty
            }
            projects = found.sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
        } catch {
            projectLoadError = error.localizedDescription
        }
    }

    func selectProject(_ project: JiraProject) {
        guard !isRunning else { return }
        config.projectKey = project.key
        config.boardID = ""
        config.currentIssue = ""
        config.save()
        isShowingProjectPicker = false
        projectSearch = ""
    }

    private func makeEnvironment() -> [String: String] {
        var environment = ProcessInfo.processInfo.environment
        environment["CARTESIA_API_KEY"] = credentials.cartesiaAPIKey
        environment["ANTHROPIC_API_KEY"] = credentials.anthropicAPIKey
        environment["JIRA_BASE_URL"] = config.jiraBaseURL
        environment["JIRA_EMAIL"] = config.jiraEmail
        environment["JIRA_API_TOKEN"] = credentials.jiraAPIToken
        environment["JIRA_PROJECT_KEY"] = config.projectKey
        environment["JIRA_BOARD_ID"] = config.boardID.isEmpty ? nil : config.boardID
        environment["JIRA_CURRENT_ISSUE"] = config.currentIssue.isEmpty ? nil : config.currentIssue
        environment["JIRA_DRY_RUN"] = config.dryRun ? "1" : "0"
        environment["ALLOW_DELETE"] = config.allowDelete ? "1" : "0"
        environment["ARGO_INPUT_DEVICE"] = config.audioDevice
        environment["ARGO_API_BASE"] = config.argoAPIBase.isEmpty ? nil : config.argoAPIBase
        environment["ARGO_API_KEY"] = credentials.argoAPIKey
        environment["PYTHONUNBUFFERED"] = "1"
        return environment
    }

    private func appendOutput(_ text: String) {
        let combined = stateMarkerCarry + text
        var remaining = combined[...]
        while let marker = remaining.range(of: "[ARGO_STATE:") {
            let valueStart = marker.upperBound
            guard let closing = remaining[valueStart...].firstIndex(of: "]") else { break }
            let name = String(remaining[valueStart..<closing])
            if voiceState != .stopping, let state = ArgoVoiceState(rawValue: name) { voiceState = state }
            remaining = remaining[remaining.index(after: closing)...]
        }
        stateMarkerCarry = String(combined.suffix(32))
        logs.append(contentsOf: text.split(whereSeparator: \.isNewline).map(String.init))
        if logs.count > 300 { logs.removeFirst(logs.count - 300) }
    }

    private func refreshProcessState() {
        isRunning = agentProcess?.isRunning ?? false
    }

    private func locateAgentDirectory() -> URL? {
        if let path = Bundle.main.object(forInfoDictionaryKey: "ArgoAgentServerPath") as? String {
            let url = URL(fileURLWithPath: path)
            if FileManager.default.fileExists(atPath: url.appendingPathComponent("cartesia_stream.py").path) {
                return url
            }
        }
        if let bundled = Bundle.main.resourceURL?.appendingPathComponent("agent-server"),
           FileManager.default.fileExists(atPath: bundled.appendingPathComponent("cartesia_stream.py").path) {
            return bundled
        }
        var source = URL(fileURLWithPath: #filePath)
        for _ in 0..<8 {
            source.deleteLastPathComponent()
            let candidate = source.appendingPathComponent("agent-server")
            if FileManager.default.fileExists(atPath: candidate.appendingPathComponent("cartesia_stream.py").path) {
                return candidate
            }
        }
        return nil
    }

    private func locatePython(agentDirectory: URL) -> URL? {
        if let path = Bundle.main.object(forInfoDictionaryKey: "ArgoPythonPath") as? String,
           FileManager.default.isExecutableFile(atPath: path) {
            return URL(fileURLWithPath: path)
        }
        let local = agentDirectory.appendingPathComponent(".venv/bin/python")
        if FileManager.default.isExecutableFile(atPath: local.path) { return local }
        for path in ["/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"] {
            if FileManager.default.isExecutableFile(atPath: path) { return URL(fileURLWithPath: path) }
        }
        return nil
    }

    private func migrateExistingDotEnv() {
        guard !didMigrateDotEnv else { return }
        didMigrateDotEnv = true
        let sourcePath = Bundle.main.object(forInfoDictionaryKey: "ArgoLegacyDotEnvPath") as? String
        let envURL = sourcePath.map { URL(fileURLWithPath: $0) }
            ?? locateAgentDirectory()?.appendingPathComponent(".env")
        guard let envURL else { return }
        guard let raw = try? String(contentsOf: envURL, encoding: .utf8) else { return }
        let values = DotEnv.parse(raw)
        if credentials.cartesiaAPIKey.isEmpty { credentials.cartesiaAPIKey = values["CARTESIA_API_KEY"] ?? "" }
        if credentials.anthropicAPIKey.isEmpty { credentials.anthropicAPIKey = values["ANTHROPIC_API_KEY"] ?? "" }
        if credentials.jiraAPIToken.isEmpty { credentials.jiraAPIToken = values["JIRA_API_TOKEN"] ?? "" }
        if credentials.argoAPIKey.isEmpty { credentials.argoAPIKey = values["ARGO_API_KEY"] ?? "" }
        try? credentials.save()
        if config.jiraBaseURL.isEmpty { config.jiraBaseURL = values["JIRA_BASE_URL"] ?? "" }
        if config.jiraEmail.isEmpty { config.jiraEmail = values["JIRA_EMAIL"] ?? "" }
        if config.projectKey == "ARGO" { config.projectKey = values["JIRA_PROJECT_KEY"] ?? "ARGO" }
        config.boardID = values["JIRA_BOARD_ID"] ?? config.boardID
        config.currentIssue = values["JIRA_CURRENT_ISSUE"] ?? config.currentIssue
        config.audioDevice = values["ARGO_INPUT_DEVICE"] ?? config.audioDevice
        config.dryRun = values["JIRA_DRY_RUN"] != "0"
        config.argoAPIBase = values["ARGO_API_BASE"] ?? config.argoAPIBase
        config.save()
    }
}

enum AppPage: String, CaseIterable, Identifiable {
    case home = "Home"
    case devInfo = "Dev info"
    var id: String { rawValue }
    var symbol: String { self == .home ? "waveform" : "wrench.and.screwdriver" }
}

struct JiraProject: Identifiable, Hashable {
    let key: String
    let name: String
    var id: String { key }
}

struct ProjectLoadError: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}

struct ArgoConfig: Codable {
    var jiraBaseURL = ""
    var jiraEmail = ""
    var projectKey = "ARGO"
    var boardID = ""
    var currentIssue = ""
    var audioDevice = "default"
    var dryRun = true
    var allowDelete = false
    var argoAPIBase = ""

    static var fileURL: URL {
        let directory = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/Argo", isDirectory: true)
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        try? FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: directory.path)
        return directory.appendingPathComponent("settings.json")
    }

    static func load() -> ArgoConfig {
        guard let data = try? Data(contentsOf: fileURL),
              let decoded = try? JSONDecoder().decode(ArgoConfig.self, from: data) else { return ArgoConfig() }
        return decoded
    }

    func save() {
        guard let data = try? JSONEncoder().encode(self) else { return }
        try? data.write(to: Self.fileURL, options: .atomic)
        try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: Self.fileURL.path)
    }
}

struct ArgoCredentials: Codable {
    var cartesiaAPIKey = ""
    var anthropicAPIKey = ""
    var jiraAPIToken = ""
    var argoAPIKey = ""

    static var fileURL: URL {
        let directory = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/Argo", isDirectory: true)
        try? FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700]
        )
        try? FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: directory.path)
        return directory.appendingPathComponent("credentials.json")
    }

    static func load() -> ArgoCredentials {
        guard let data = try? Data(contentsOf: fileURL),
              let credentials = try? JSONDecoder().decode(ArgoCredentials.self, from: data) else {
            return ArgoCredentials()
        }
        return credentials
    }

    func save() throws {
        let data = try JSONEncoder().encode(self)
        try data.write(to: Self.fileURL, options: .atomic)
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o600],
            ofItemAtPath: Self.fileURL.path
        )
    }
}

enum DotEnv {
    static func parse(_ text: String) -> [String: String] {
        var values: [String: String] = [:]
        for line in text.split(whereSeparator: \.isNewline) {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            guard !trimmed.isEmpty, !trimmed.hasPrefix("#"), let separator = trimmed.firstIndex(of: "=") else { continue }
            let key = String(trimmed[..<separator]).trimmingCharacters(in: .whitespaces)
            var value = String(trimmed[trimmed.index(after: separator)...]).trimmingCharacters(in: .whitespaces)
            if value.count >= 2, (value.first == "'" && value.last == "'") || (value.first == "\"" && value.last == "\"") {
                value.removeFirst()
                value.removeLast()
            }
            if !key.isEmpty { values[key] = value }
        }
        return values
    }
}

struct ArgoRootView: View {
    @EnvironmentObject private var model: ArgoModel

    var body: some View {
        Group {
            if !model.isConfigured {
                ConnectView()
            } else {
                NavigationSplitView {
                    List(selection: $model.selectedPage) {
                        ForEach(AppPage.allCases) { page in
                            Label(page.rawValue, systemImage: page.symbol).tag(page)
                        }
                    }
                    .listStyle(.sidebar)
                    .navigationTitle("Argo")
                    .safeAreaInset(edge: .bottom) {
                        HStack(spacing: 9) {
                            Circle().fill(model.isRunning ? Color.green : Color.gray.opacity(0.55)).frame(width: 8, height: 8)
                            Text(model.isRunning ? "Listening" : "Ready")
                                .font(.system(size: 12, weight: .medium)).foregroundStyle(.secondary)
                            Spacer()
                        }.padding(.horizontal, 16).padding(.vertical, 12)
                    }
                } detail: {
                    if model.selectedPage == .devInfo { DeveloperSettingsView() }
                    else { HomeView() }
                }
                .navigationSplitViewStyle(.balanced)
            }
        }
        .sheet(isPresented: $model.isShowingProjectPicker) {
            ProjectPickerView().environmentObject(model).frame(minWidth: 460, minHeight: 560)
        }
        .tint(Color(red: 0.16, green: 0.40, blue: 0.84))
        .alert("Argo", isPresented: Binding(
            get: { model.errorMessage != nil },
            set: { if !$0 { model.clearMessages() } }
        )) {
            Button("OK", role: .cancel) { model.clearMessages() }
        } message: { Text(model.errorMessage ?? "") }
    }
}

struct ConnectView: View {
    @EnvironmentObject private var model: ArgoModel

    var body: some View {
        ZStack {
            Color(red: 0.965, green: 0.973, blue: 0.99).ignoresSafeArea()
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 12) {
                    ArgoLogo(size: 46)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Argo").font(.system(size: 24, weight: .bold, design: .rounded))
                        Text("Your voice-powered Jira teammate").foregroundStyle(.secondary)
                    }
                }.padding(.bottom, 24)
                Text("Connect your accounts").font(.system(size: 25, weight: .bold, design: .rounded))
                Text("These are the only details needed to get Argo listening. Your credentials stay on this Mac.")
                    .foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true).padding(.top, 6).padding(.bottom, 20)

                VStack(alignment: .leading, spacing: 14) {
                    SecretField(title: "Cartesia API key", subtitle: "Powers voice recognition and Argo's spoken replies.", placeholder: model.hasCartesiaKey ? "Saved securely · leave blank to keep" : "Paste your Cartesia API key", text: $model.cartesiaInput, saved: model.hasCartesiaKey)
                    SecretField(title: "Claude API key", subtitle: "Understands your Jira requests.", placeholder: model.hasAnthropicKey ? "Saved securely · leave blank to keep" : "Paste your Anthropic API key", text: $model.anthropicInput, saved: model.hasAnthropicKey)
                    HStack(spacing: 12) {
                        LabeledInput(title: "Jira site", placeholder: "https://your-site.atlassian.net", text: $model.config.jiraBaseURL)
                        LabeledInput(title: "Atlassian account email", placeholder: "you@example.com", text: $model.config.jiraEmail)
                    }
                    SecretField(title: "Jira API token", subtitle: "Use an API token from the Atlassian account with access to your project.", placeholder: model.hasJiraToken ? "Saved securely · leave blank to keep" : "Paste your Jira API token", text: $model.jiraTokenInput, saved: model.hasJiraToken)
                }
                HStack(spacing: 16) {
                    Link(destination: URL(string: "https://id.atlassian.com/manage-profile/security/api-tokens")!) {
                        Label("Get a Jira token", systemImage: "arrow.up.right.square").font(.system(size: 12, weight: .medium))
                    }
                    Link(destination: URL(string: "https://console.anthropic.com/settings/keys")!) {
                        Label("Get a Claude key", systemImage: "arrow.up.right.square").font(.system(size: 12, weight: .medium))
                    }
                }.padding(.top, 13)
                HStack {
                    if let info = model.infoMessage { Label(info, systemImage: "checkmark.circle.fill").foregroundStyle(.green).font(.system(size: 12)) }
                    Spacer()
                    Button(action: model.saveCredentials) {
                        HStack(spacing: 8) { Text("Connect and continue"); Image(systemName: "arrow.right") }
                            .font(.system(size: 14, weight: .semibold)).padding(.horizontal, 17).padding(.vertical, 10)
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(!canContinue)
                }.padding(.top, 20)
                if !canContinue {
                    Text("Enter the required connections to continue.").font(.system(size: 11)).foregroundStyle(.secondary).padding(.top, 7)
                }
            }
            .padding(30).frame(maxWidth: 620).background(.white).clipShape(RoundedRectangle(cornerRadius: 21))
            .shadow(color: .black.opacity(0.08), radius: 30, y: 12)
        }
    }

    private var canContinue: Bool {
        (model.hasCartesiaKey || !model.cartesiaInput.isEmpty) &&
        (model.hasAnthropicKey || !model.anthropicInput.isEmpty) &&
        (model.hasJiraToken || !model.jiraTokenInput.isEmpty) &&
        !model.config.jiraBaseURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty &&
        !model.config.jiraEmail.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }
}

struct HomeView: View {
    @EnvironmentObject private var model: ArgoModel

    var body: some View {
        VStack(alignment: .leading, spacing: 22) {
            HStack(alignment: .top) {
                ArgoLogo(size: 42)
                VStack(alignment: .leading, spacing: 5) {
                    Text("Your Jira teammate").font(.system(size: 13, weight: .medium)).foregroundStyle(.secondary)
                    Text("Welcome to Argo").font(.system(size: 30, weight: .bold, design: .rounded))
                }
                Spacer()
                Button { model.selectedPage = .devInfo } label: {
                    Label("Dev info", systemImage: "wrench.and.screwdriver")
                }.buttonStyle(.bordered)
            }

            HStack(spacing: 14) {
                Image(systemName: "square.grid.2x2.fill").font(.system(size: 19)).foregroundStyle(.blue)
                    .frame(width: 46, height: 46).background(Color.blue.opacity(0.09)).clipShape(RoundedRectangle(cornerRadius: 13))
                VStack(alignment: .leading, spacing: 3) {
                    Text("WORKING IN").font(.system(size: 10, weight: .semibold)).tracking(0.8).foregroundStyle(.secondary)
                    Text(model.currentProjectName).font(.system(size: 16, weight: .semibold))
                    Text(model.config.projectKey).font(.system(size: 11, design: .monospaced)).foregroundStyle(.secondary)
                }
                Spacer()
                Button("Change project") { model.isShowingProjectPicker = true }
                    .buttonStyle(.bordered)
                    .disabled(model.isRunning)
            }
            .padding(16).background(.white).clipShape(RoundedRectangle(cornerRadius: 17))
            .overlay(RoundedRectangle(cornerRadius: 17).stroke(Color.black.opacity(0.06)))
            if model.isRunning {
                Text("Stop Argo before switching projects.").font(.system(size: 11)).foregroundStyle(.secondary)
                    .padding(.top, -15)
            }

            VStack(spacing: 17) {
                VoiceOrbView(state: model.isRunning ? model.voiceState : .idle)
                VStack(spacing: 5) {
                    Text(model.isRunning ? model.voiceState.title : ArgoVoiceState.idle.title)
                        .font(.system(size: 21, weight: .semibold, design: .rounded))
                    Text(model.isRunning ? model.voiceState.subtitle : ArgoVoiceState.idle.subtitle)
                        .font(.system(size: 13)).foregroundStyle(.secondary)
                }
                Group {
                    if model.isRunning {
                        Button(action: model.stopAgent) {
                            Label(model.voiceState == .stopping ? "Stopping…" : "Stop Argo",
                                  systemImage: model.voiceState == .stopping ? "hourglass" : "stop.fill")
                                .frame(minWidth: 150)
                        }
                        .buttonStyle(.bordered)
                        .disabled(model.voiceState == .stopping)
                    } else {
                        Button(action: model.startAgent) { Label("Start listening", systemImage: "mic.fill").frame(minWidth: 150) }
                            .buttonStyle(.borderedProminent)
                    }
                }.controlSize(.large)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .padding(28).background(.white).clipShape(RoundedRectangle(cornerRadius: 20))
            .overlay(RoundedRectangle(cornerRadius: 20).stroke(Color.black.opacity(0.06)))
        }
        .padding(27).background(Color(red: 0.965, green: 0.973, blue: 0.99))
    }
}

struct VoiceOrbView: View {
    let state: ArgoVoiceState
    @State private var pulse = false

    var body: some View {
        ZStack {
            Circle()
                .fill(state.color.opacity(state == .idle ? 0.06 : 0.12))
                .frame(width: 132, height: 132)
                .scaleEffect(state.animates && pulse ? 1.14 : 0.92)
            Circle()
                .stroke(state.color.opacity(state == .idle ? 0.16 : 0.32), lineWidth: 1.5)
                .frame(width: 100, height: 100)
                .scaleEffect(state.animates && pulse ? 1.08 : 0.94)
            Circle()
                .fill(LinearGradient(
                    colors: [state.color.opacity(0.72), state.color],
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                ))
                .frame(width: 78, height: 78)
                .shadow(color: state.color.opacity(0.25), radius: 14, y: 6)
            Image(systemName: state.symbol)
                .font(.system(size: 28, weight: .medium))
                .foregroundStyle(.white)
        }
        .frame(width: 132, height: 132)
        .onAppear { pulse = true }
        .onChange(of: state) { _, _ in pulse.toggle() }
        .animation(
            .easeInOut(duration: state == .speaking ? 0.38 : 0.95).repeatForever(autoreverses: true),
            value: pulse
        )
        .accessibilityLabel(state.title)
    }
}

struct ArgoLogo: View {
    let size: CGFloat

    @ViewBuilder
    var body: some View {
        if let url = Bundle.main.url(forResource: "argo_logo", withExtension: "png"),
           let image = NSImage(contentsOf: url) {
            Image(nsImage: image)
                .resizable()
                .aspectRatio(contentMode: .fit)
                .frame(width: size, height: size)
                .clipShape(RoundedRectangle(cornerRadius: size * 0.22))
        } else {
            Image(systemName: "sailboat.fill")
                .resizable()
                .aspectRatio(contentMode: .fit)
                .frame(width: size, height: size)
                .foregroundStyle(.blue)
        }
    }
}

struct ProjectPickerView: View {
    @EnvironmentObject private var model: ArgoModel

    var body: some View {
        VStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Choose a project").font(.system(size: 23, weight: .bold, design: .rounded))
                        Text("Argo will use this project for your Jira requests.").font(.system(size: 12)).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button { model.isShowingProjectPicker = false } label: { Image(systemName: "xmark.circle.fill").foregroundStyle(.tertiary) }
                        .buttonStyle(.plain)
                }
                TextField("Search projects by name or key", text: $model.projectSearch)
                    .textFieldStyle(.roundedBorder)
                if model.isLoadingProjects {
                    ProgressView("Loading your Jira projects…").font(.system(size: 12)).padding(.vertical, 8)
                } else if let error = model.projectLoadError {
                    Label(error, systemImage: "exclamationmark.triangle.fill")
                        .font(.system(size: 12)).foregroundStyle(.orange).fixedSize(horizontal: false, vertical: true)
                    Button("Try again") { Task { await model.loadProjects() } }
                        .font(.system(size: 12, weight: .medium))
                }
            }.padding(20)

            if model.filteredProjects.isEmpty && !model.isLoadingProjects && model.projectLoadError == nil {
                ContentUnavailableView("No projects found", systemImage: "square.grid.2x2", description: Text("Your Jira account may not have access to any projects."))
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                List(model.filteredProjects) { project in
                    Button { model.selectProject(project) } label: {
                        HStack(spacing: 12) {
                            Image(systemName: "square.grid.2x2.fill").foregroundStyle(.blue)
                                .frame(width: 34, height: 34).background(Color.blue.opacity(0.08)).clipShape(RoundedRectangle(cornerRadius: 9))
                            VStack(alignment: .leading, spacing: 3) {
                                Text(project.name).font(.system(size: 13, weight: .semibold)).foregroundStyle(.primary)
                                Text(project.key).font(.system(size: 11, design: .monospaced)).foregroundStyle(.secondary)
                            }
                            Spacer()
                            if project.key == model.config.projectKey { Image(systemName: "checkmark.circle.fill").foregroundStyle(.blue) }
                        }.contentShape(Rectangle())
                    }.buttonStyle(.plain).disabled(model.isRunning)
                }.listStyle(.plain)
            }
            HStack {
                Text("\(model.projects.count) projects").font(.system(size: 11)).foregroundStyle(.secondary)
                Spacer()
                Button("Refresh") { Task { await model.loadProjects() } }
                    .disabled(model.isLoadingProjects)
            }.padding(14).background(Color.black.opacity(0.025))
        }
        .background(Color(red: 0.965, green: 0.973, blue: 0.99))
        .task { if model.projects.isEmpty { await model.loadProjects() } }
    }
}

struct DeveloperSettingsView: View {
    @EnvironmentObject private var model: ArgoModel

    var body: some View {
        Form {
            Section {
                Text("Developer info").font(.system(size: 25, weight: .bold, design: .rounded))
                Text("These options are for configuring Argo's Jira workspace and audio routing.").foregroundStyle(.secondary)
                    .font(.system(size: 13))
            }
            Section("Jira") {
                LabeledContent("Current project", value: "\(model.currentProjectName) (\(model.config.projectKey))")
                Button("Change project…") { model.isShowingProjectPicker = true }
                    .disabled(model.isRunning)
                if model.isRunning {
                    Text("Stop Argo before switching projects.").font(.system(size: 11)).foregroundStyle(.secondary)
                }
                TextField("Board ID (optional; auto-detected if blank)", text: $model.config.boardID).textFieldStyle(.roundedBorder)
                TextField("Current issue key (optional)", text: $model.config.currentIssue).textFieldStyle(.roundedBorder)
                Toggle("Dry run — don't apply Jira changes", isOn: $model.config.dryRun)
                Toggle("Allow voice commands to delete issues", isOn: $model.config.allowDelete)
                    .tint(.red)
                Text("Read requests can still query Jira while dry run is on. Deletion is disabled unless you explicitly allow it here.")
                    .font(.system(size: 11)).foregroundStyle(.secondary)
            }
            Section("Audio") {
                TextField("Input device", text: $model.config.audioDevice, prompt: Text("default or BlackHole 2ch"))
                    .textFieldStyle(.roundedBorder)
                Text("Use “default” for your Mac microphone, or enter a device name such as BlackHole 2ch.")
                    .font(.system(size: 11)).foregroundStyle(.secondary)
            }
            Section("Meeting history (developer option)") {
                TextField("API base URL", text: $model.config.argoAPIBase).textFieldStyle(.roundedBorder)
                SecureField(model.hasArgoApiKey ? "API key saved · enter a replacement" : "API key", text: $model.argoApiKeyInput)
                    .textFieldStyle(.roundedBorder)
            }
            Section("Connections") {
                CredentialStatus(name: "Cartesia", connected: model.hasCartesiaKey)
                CredentialStatus(name: "Claude", connected: model.hasAnthropicKey)
                CredentialStatus(name: "Jira", connected: model.hasJiraToken)
                Button("Edit required credentials") { model.selectedPage = .home }
                    .font(.system(size: 12))
            }
            Section("Diagnostics") {
                DisclosureGroup("Session logs") {
                    VStack(alignment: .leading, spacing: 8) {
                        HStack {
                            Text("Recent output from the voice agent.").font(.system(size: 11)).foregroundStyle(.secondary)
                            Spacer()
                            Button("Refresh projects") { Task { await model.loadProjects() } }
                                .disabled(model.isLoadingProjects)
                        }
                        if model.logs.isEmpty {
                            Text("No session output yet.").font(.system(size: 12)).foregroundStyle(.secondary)
                                .frame(maxWidth: .infinity, minHeight: 80, alignment: .center)
                        } else {
                            ScrollView {
                                LazyVStack(alignment: .leading, spacing: 5) {
                                    ForEach(Array(model.logs.enumerated()), id: \.offset) { _, line in
                                        Text(line).font(.system(size: 11, design: .monospaced))
                                            .foregroundStyle(line.localizedCaseInsensitiveContains("error") ? Color.red : Color.secondary)
                                            .frame(maxWidth: .infinity, alignment: .leading)
                                    }
                                }.padding(10)
                            }.frame(height: 200).background(Color.black.opacity(0.035)).clipShape(RoundedRectangle(cornerRadius: 8))
                        }
                    }.padding(.top, 8)
                }
            }
            HStack {
                if let info = model.infoMessage { Label(info, systemImage: "checkmark.circle.fill").foregroundStyle(.green) }
                Spacer()
                Button("Save settings", action: model.saveDeveloperSettings).buttonStyle(.borderedProminent)
            }.listRowBackground(Color.clear)
        }
        .formStyle(.grouped)
        .scrollContentBackground(.hidden)
        .background(Color(red: 0.965, green: 0.973, blue: 0.99))
        .padding(20)
    }
}

struct SecretField: View {
    let title: String
    let subtitle: String
    let placeholder: String
    @Binding var text: String
    let saved: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack {
                Text(title).font(.system(size: 13, weight: .semibold))
                Spacer()
                if saved { Label("Connected", systemImage: "checkmark.circle.fill").font(.system(size: 11, weight: .medium)).foregroundStyle(.green) }
            }
            SecureField(placeholder, text: $text).textFieldStyle(.roundedBorder)
            Text(subtitle).font(.system(size: 11)).foregroundStyle(.secondary)
        }
    }
}

struct LabeledInput: View {
    let title: String
    let placeholder: String
    @Binding var text: String
    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(title).font(.system(size: 13, weight: .semibold))
            TextField(placeholder, text: $text).textFieldStyle(.roundedBorder)
        }
    }
}

struct SummaryTile: View {
    let title: String
    let value: String
    let symbol: String
    var body: some View {
        HStack(spacing: 11) {
            Image(systemName: symbol).foregroundStyle(.blue).frame(width: 32, height: 32).background(Color.blue.opacity(0.08)).clipShape(RoundedRectangle(cornerRadius: 9))
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.system(size: 11)).foregroundStyle(.secondary)
                Text(value).font(.system(size: 13, weight: .semibold)).lineLimit(1)
            }
            Spacer(minLength: 0)
        }.padding(13).frame(maxWidth: .infinity, alignment: .leading).background(.white).clipShape(RoundedRectangle(cornerRadius: 14)).overlay(RoundedRectangle(cornerRadius: 14).stroke(Color.black.opacity(0.06)))
    }
}

struct CredentialStatus: View {
    let name: String
    let connected: Bool
    var body: some View {
        HStack {
            Text(name)
            Spacer()
            Label(connected ? "Connected" : "Needs setup", systemImage: connected ? "checkmark.circle.fill" : "exclamationmark.circle")
                .foregroundStyle(connected ? .green : .orange).font(.system(size: 12))
        }
    }
}
