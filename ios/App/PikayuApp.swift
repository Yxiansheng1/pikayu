import ReplayKit
import SwiftUI

@main
struct PikayuApp: App {
    var body: some Scene {
        WindowGroup { ContentView() }
    }
}

let kServerURL = Bundle.main.object(forInfoDictionaryKey: "PikayuServerURL") as? String
    ?? "http://100.108.35.123:8765"

struct ContentView: View {
    @StateObject private var pip = HintPiP(serverURL: kServerURL)

    var body: some View {
        ScrollView {
            VStack(spacing: 16) {
                Text("象棋提示").font(.title2).bold()

                HStack {
                    Circle().fill(pip.connection == "已连接" ? Color.green : Color.red).frame(width: 8, height: 8)
                    Text("服务器：\(pip.connection)").font(.footnote)
                    Spacer()
                    Text(kServerURL).font(.caption2).foregroundStyle(.secondary)
                }
                .padding(.horizontal)

                // 悬浮窗预览（画中画就是把这块画面浮到其他 App 上）
                PiPPreview(layer: pip.displayLayer)
                    .frame(width: 300, height: 120)

                step(1, "开始采集", "点下面的圆形按钮 → 选「象棋提示采集」→ 开始直播")
                BroadcastPicker()
                    .frame(width: 72, height: 72)
                    .background(Color.accentColor.opacity(0.12), in: Circle())

                step(2, "开启悬浮窗", pip.pipActive ? "悬浮窗已开启，可以切到 JJ象棋 了" : "点下面按钮，或直接回到桌面也会自动浮起")
                Button(pip.pipActive ? "关闭悬浮窗" : "开启悬浮窗") { pip.togglePiP() }
                    .buttonStyle(.borderedProminent)
                    .disabled(!pip.pipPossible && !pip.pipActive)

                step(3, "去下棋", "切到 JJ象棋，把悬浮窗拖到棋盘上方或下方的空白处（双指可缩小），别挡住棋盘")
            }
            .padding(.vertical, 24)
        }
        .onAppear { pip.start() }
    }

    private func step(_ n: Int, _ title: String, _ detail: String) -> some View {
        VStack(spacing: 2) {
            Text("\(n). \(title)").font(.headline)
            Text(detail).font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center)
        }
        .padding(.horizontal)
    }
}

struct BroadcastPicker: UIViewRepresentable {
    func makeUIView(context: Context) -> RPSystemBroadcastPickerView {
        let v = RPSystemBroadcastPickerView(frame: CGRect(x: 0, y: 0, width: 72, height: 72))
        // 侧载重签时 Bundle ID 可能被改写，所以用"主 App ID + .broadcast"动态拼
        if let id = Bundle.main.bundleIdentifier {
            v.preferredExtension = id + ".broadcast"
        }
        v.showsMicrophoneButton = false
        return v
    }

    func updateUIView(_ uiView: RPSystemBroadcastPickerView, context: Context) {}
}
