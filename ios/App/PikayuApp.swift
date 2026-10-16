import ReplayKit
import SwiftUI

@main
struct PikayuApp: App {
    var body: some Scene {
        WindowGroup { ContentView() }
    }
}

struct ContentView: View {
    private let serverURL = Bundle.main.object(forInfoDictionaryKey: "PikayuServerURL") as? String
        ?? "http://100.108.35.123:8765"
    @State private var health = "未检测"

    var body: some View {
        VStack(spacing: 20) {
            Text("象棋提示 · 链路验证").font(.title2).bold()

            VStack(alignment: .leading, spacing: 6) {
                Text("服务器：\(serverURL)").font(.footnote).foregroundStyle(.secondary)
                HStack {
                    Text("连接：\(health)").font(.footnote)
                    Spacer()
                    Button("检测") { checkHealth() }.font(.footnote)
                }
            }
            .padding()
            .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 12))

            // 系统广播选择器：点它弹出系统面板，选"象棋提示采集"后点"开始直播"
            BroadcastPicker()
                .frame(width: 88, height: 88)
                .background(Color.accentColor.opacity(0.12), in: Circle())
            Text("点上面的圆形按钮 → 选择「象棋提示采集」→ 开始直播\n然后切到 JJ象棋 正常下棋")
                .font(.footnote)
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)

            Text("在电脑浏览器打开\n\(serverURL)/frames\n可实时看到传回的画面和内存占用")
                .font(.footnote)
                .multilineTextAlignment(.center)
            Spacer()
        }
        .padding(24)
        .onAppear { checkHealth() }
    }

    private func checkHealth() {
        health = "检测中…"
        guard let url = URL(string: serverURL + "/health") else { return }
        var req = URLRequest(url: url)
        req.timeoutInterval = 5
        URLSession.shared.dataTask(with: req) { data, _, error in
            let text: String
            if let error {
                text = "失败：\(error.localizedDescription)"
            } else if let data, String(decoding: data, as: UTF8.self).contains("\"ok\":true") {
                text = "正常 ✅"
            } else {
                text = "异常响应"
            }
            DispatchQueue.main.async { health = text }
        }.resume()
    }
}

struct BroadcastPicker: UIViewRepresentable {
    func makeUIView(context: Context) -> RPSystemBroadcastPickerView {
        let v = RPSystemBroadcastPickerView(frame: CGRect(x: 0, y: 0, width: 88, height: 88))
        // 侧载重签时 Bundle ID 可能被改写，所以用"主 App ID + .broadcast"动态拼，而不是写死
        if let id = Bundle.main.bundleIdentifier {
            v.preferredExtension = id + ".broadcast"
        }
        v.showsMicrophoneButton = false
        return v
    }

    func updateUIView(_ uiView: RPSystemBroadcastPickerView, context: Context) {}
}
