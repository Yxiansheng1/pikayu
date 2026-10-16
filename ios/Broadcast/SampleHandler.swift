import CoreImage
import ReplayKit
import UIKit

/// 屏幕广播扩展：每秒抽一帧 -> 缩放到 720 宽 -> JPEG -> POST 到家里电脑 /frame。
/// 系统给广播扩展的内存上限约 50MB，超了会被静默杀掉，所以：
///   - 上一帧没传完就丢帧，绝不排队积压
///   - CIContext 只建一次，每帧在 autoreleasepool 里处理
///   - 每帧带上当前内存占用（X-Mem-MB），服务端监控页直接看曲线
class SampleHandler: RPBroadcastSampleHandler {

    private let interval: CFTimeInterval = 1.0      // 抽帧间隔（秒）
    private let targetWidth: CGFloat = 720          // 缩放后宽度（像素）
    private let jpegQuality: CGFloat = 0.6

    private let ciContext = CIContext(options: [.cacheIntermediates: false])
    private let colorSpace = CGColorSpaceCreateDeviceRGB()
    private lazy var session: URLSession = {
        let c = URLSessionConfiguration.ephemeral
        c.timeoutIntervalForRequest = 5
        c.httpMaximumConnectionsPerHost = 1
        c.urlCache = nil
        return URLSession(configuration: c)
    }()
    private lazy var frameURL: URL = {
        let s = Bundle.main.object(forInfoDictionaryKey: "PikayuServerURL") as? String
            ?? "http://100.108.35.123:8765"
        return URL(string: s)!.appendingPathComponent("frame")
    }()

    private let lock = NSLock()
    private var inFlight = false
    private var lastSent: CFTimeInterval = 0
    private var seq = 0
    private var sent = 0
    private var dropped = 0
    private var failed = 0

    override func broadcastStarted(withSetupInfo setupInfo: [String: NSObject]?) {
        postEvent("start")
    }

    override func broadcastFinished() {
        postEvent("finish")
    }

    override func processSampleBuffer(_ sampleBuffer: CMSampleBuffer, with sampleBufferType: RPSampleBufferType) {
        guard sampleBufferType == .video else { return }
        let now = CACurrentMediaTime()

        lock.lock()
        let busy = inFlight
        let due = now - lastSent >= interval
        if due && busy { dropped += 1 }
        if !due || busy { lock.unlock(); return }
        inFlight = true
        lastSent = now
        seq += 1
        let mySeq = seq
        lock.unlock()

        guard let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else {
            setIdle()
            return
        }

        var jpeg: Data?
        autoreleasepool {
            var image = CIImage(cvPixelBuffer: pixelBuffer)
            let scale = min(1, targetWidth / image.extent.width)
            image = image.transformed(by: CGAffineTransform(scaleX: scale, y: scale))
            jpeg = ciContext.jpegRepresentation(
                of: image, colorSpace: colorSpace,
                options: [kCGImageDestinationLossyCompressionQuality as CIImageRepresentationOption: jpegQuality])
        }
        guard let data = jpeg else {
            setIdle()
            return
        }
        upload(data, seq: mySeq)
    }

    private func upload(_ data: Data, seq: Int) {
        var req = URLRequest(url: frameURL)
        req.httpMethod = "POST"
        req.setValue("image/jpeg", forHTTPHeaderField: "Content-Type")
        req.setValue(String(seq), forHTTPHeaderField: "X-Seq")
        req.setValue(String(format: "%.1f", Self.memoryMB()), forHTTPHeaderField: "X-Mem-MB")
        lock.lock()
        req.setValue("\(sent)/\(dropped)/\(failed)", forHTTPHeaderField: "X-Stats")
        lock.unlock()

        session.uploadTask(with: req, from: data) { [weak self] _, response, error in
            guard let self else { return }
            self.lock.lock()
            if error == nil, (response as? HTTPURLResponse)?.statusCode == 200 {
                self.sent += 1
            } else {
                self.failed += 1
            }
            self.inFlight = false
            self.lock.unlock()
        }.resume()
    }

    private func setIdle() {
        lock.lock()
        inFlight = false
        lock.unlock()
    }

    private func postEvent(_ name: String) {
        var req = URLRequest(url: frameURL.deletingLastPathComponent().appendingPathComponent("frame/event"))
        req.httpMethod = "POST"
        req.setValue(name, forHTTPHeaderField: "X-Event")
        req.setValue(String(format: "%.1f", Self.memoryMB()), forHTTPHeaderField: "X-Mem-MB")
        session.dataTask(with: req).resume()
    }

    /// 当前进程实际内存占用（phys_footprint，系统判断是否超限用的就是它）
    static func memoryMB() -> Double {
        var info = task_vm_info_data_t()
        var count = mach_msg_type_number_t(MemoryLayout<task_vm_info_data_t>.size / MemoryLayout<natural_t>.size)
        let kr = withUnsafeMutablePointer(to: &info) {
            $0.withMemoryRebound(to: integer_t.self, capacity: Int(count)) {
                task_info(mach_task_self_, task_flavor_t(TASK_VM_INFO), $0, &count)
            }
        }
        return kr == KERN_SUCCESS ? Double(info.phys_footprint) / 1_048_576 : -1
    }
}
