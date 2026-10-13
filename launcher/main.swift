import AppKit

// 本机启动器，不打包用户任务、密钥或 Python 环境。
final class Launcher: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        let bundle = Bundle.main
        let fallback = bundle.bundleURL.deletingLastPathComponent()
        let configured = bundle.url(forResource: "product-root", withExtension: "txt")
            .flatMap { try? String(contentsOf: $0, encoding: .utf8) }
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
        let root = configured.map { URL(fileURLWithPath: $0, isDirectory: true) } ?? fallback
        let script = root.appendingPathComponent("启动听文声影工作台.command")
        guard FileManager.default.fileExists(atPath: script.path) else {
            failure("找不到工作台源码目录。请在产品目录重新运行“安装桌面启动器.command”，不要移动或删除源码文件夹。")
            return
        }
        DispatchQueue.global(qos: .userInitiated).async {
            let process = Process()
            let pipe = Pipe()
            process.executableURL = URL(fileURLWithPath: "/bin/zsh")
            process.arguments = [script.path]
            process.currentDirectoryURL = root
            process.standardOutput = pipe
            process.standardError = pipe
            do {
                try process.run()
                let output = pipe.fileHandleForReading.readDataToEndOfFile()
                process.waitUntilExit()
                let status = process.terminationStatus
                let message = String(data: output, encoding: .utf8) ?? "启动失败，请查看工作台日志。"
                DispatchQueue.main.async {
                    if status == 0 {
                        NSApplication.shared.terminate(nil)
                    } else {
                        self.failure(message + "\n源码位置：" + root.path)
                    }
                }
            } catch {
                DispatchQueue.main.async { self.failure("启动失败，请重新安装桌面启动器，并检查本机 Python 环境。") }
            }
        }
    }

    private func failure(_ message: String) {
        NSApplication.shared.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.messageText = "听文声影工作台无法启动"
        alert.informativeText = message
        alert.alertStyle = .warning
        alert.addButton(withTitle: "知道了")
        alert.runModal()
        NSApplication.shared.terminate(nil)
    }
}

let application = NSApplication.shared
application.setActivationPolicy(.accessory)
let delegate = Launcher()
application.delegate = delegate
application.run()
