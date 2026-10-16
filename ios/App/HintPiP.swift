import AVFoundation
import AVKit
import CoreMedia
import SwiftUI
import UIKit

// MARK: - 服务端 /hint 返回的数据

struct HintData: Decodable {
    var seq = 0
    var status = "waiting"
    var text = "等待画面…"
    var move = ""
    var score = ""
    var pv: [String] = []
    var userSide = ""
    var sideToMove = ""
    var board: [String] = []
    var arrow: [Int] = []
    var latencyMs = 0
    var frameAgeS: Double?

    init() {}

    enum K: String, CodingKey {
        case seq, status, text, move, score, pv, board, arrow
        case userSide = "user_side", sideToMove = "side_to_move"
        case latencyMs = "latency_ms", frameAgeS = "frame_age_s"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: K.self)
        seq = try c.decodeIfPresent(Int.self, forKey: .seq) ?? 0
        status = try c.decodeIfPresent(String.self, forKey: .status) ?? "waiting"
        text = try c.decodeIfPresent(String.self, forKey: .text) ?? ""
        move = try c.decodeIfPresent(String.self, forKey: .move) ?? ""
        score = try c.decodeIfPresent(String.self, forKey: .score) ?? ""
        pv = try c.decodeIfPresent([String].self, forKey: .pv) ?? []
        userSide = try c.decodeIfPresent(String.self, forKey: .userSide) ?? ""
        sideToMove = try c.decodeIfPresent(String.self, forKey: .sideToMove) ?? ""
        board = try c.decodeIfPresent([String].self, forKey: .board) ?? []
        arrow = try c.decodeIfPresent([Int].self, forKey: .arrow) ?? []
        latencyMs = try c.decodeIfPresent(Int.self, forKey: .latencyMs) ?? 0
        frameAgeS = try c.decodeIfPresent(Double.self, forKey: .frameAgeS)
    }
}

// MARK: - 轮询 + 渲染 + 画中画

/// 每 0.5 秒拉一次 /hint，把提示画成一帧视频，喂给 AVSampleBufferDisplayLayer，
/// 再用画中画把这个"视频"浮在其他 App（JJ象棋）上方。
final class HintPiP: NSObject, ObservableObject {
    @Published var hint = HintData()
    @Published var connection = "未连接"
    @Published var pipActive = false
    @Published var pipPossible = false

    let displayLayer = AVSampleBufferDisplayLayer()
    private var pip: AVPictureInPictureController?
    private var timer: DispatchSourceTimer?
    private let hintURL: URL
    private var lastBuffer: CVPixelBuffer?
    private var lastEnqueue = Date.distantPast
    private var started = false

    static let size = CGSize(width: 360, height: 480)

    init(serverURL: String) {
        hintURL = URL(string: serverURL + "/hint")!
        super.init()
        displayLayer.videoGravity = .resizeAspect
        var tb: CMTimebase?
        CMTimebaseCreateWithSourceClock(allocator: kCFAllocatorDefault, sourceClock: CMClockGetHostTimeClock(), timebaseOut: &tb)
        if let tb {
            CMTimebaseSetTime(tb, time: CMClockGetTime(CMClockGetHostTimeClock()))
            CMTimebaseSetRate(tb, rate: 1)
            displayLayer.controlTimebase = tb
        }
    }

    func start() {
        guard !started else { return }
        started = true
        // 画中画需要"正在播放媒体"的音频会话；mixWithOthers 让 JJ 自己的声音照常播放
        try? AVAudioSession.sharedInstance().setCategory(.playback, mode: .moviePlayback, options: [.mixWithOthers])
        try? AVAudioSession.sharedInstance().setActive(true)

        render(hint)
        if AVPictureInPictureController.isPictureInPictureSupported() {
            let source = AVPictureInPictureController.ContentSource(sampleBufferDisplayLayer: displayLayer, playbackDelegate: self)
            let p = AVPictureInPictureController(contentSource: source)
            p.delegate = self
            p.canStartPictureInPictureAutomaticallyFromInline = true  // 回到桌面/切 App 时自动浮起
            p.requiresLinearPlayback = true                          // 隐藏快进快退按钮
            pip = p
        }

        let t = DispatchSource.makeTimerSource(queue: .global(qos: .userInitiated))
        t.schedule(deadline: .now(), repeating: 0.5)
        t.setEventHandler { [weak self] in self?.tick() }
        t.resume()
        timer = t
    }

    func togglePiP() {
        guard let pip else { return }
        if pip.isPictureInPictureActive { pip.stopPictureInPicture() } else { pip.startPictureInPicture() }
    }

    private func tick() {
        var req = URLRequest(url: hintURL)
        req.timeoutInterval = 2
        URLSession.shared.dataTask(with: req) { [weak self] data, _, error in
            guard let self else { return }
            DispatchQueue.main.async {
                self.pipPossible = self.pip?.isPictureInPicturePossible ?? false
                if let data, error == nil, let h = try? JSONDecoder().decode(HintData.self, from: data) {
                    self.connection = "已连接"
                    let stale = (h.frameAgeS ?? 99) > 5
                    let changed = h.seq != self.hint.seq || stale != ((self.hint.frameAgeS ?? 99) > 5)
                    self.hint = h
                    if changed { self.render(h) }
                } else {
                    let wasOK = self.connection == "已连接"
                    self.connection = "连不上服务器"
                    if wasOK { var h = self.hint; h.status = "offline"; self.render(h) }
                }
                // 画中画里的"视频"至少每秒要有新帧，否则会显示加载中
                if Date().timeIntervalSince(self.lastEnqueue) > 1, let pb = self.lastBuffer {
                    self.enqueue(pb)
                }
            }
        }.resume()
    }

    // MARK: 渲染

    private func render(_ h: HintData) {
        guard let pb = HintRenderer.pixelBuffer(for: h, size: Self.size) else { return }
        lastBuffer = pb
        enqueue(pb)
    }

    private func enqueue(_ pb: CVPixelBuffer) {
        var fmt: CMVideoFormatDescription?
        CMVideoFormatDescriptionCreateForImageBuffer(allocator: nil, imageBuffer: pb, formatDescriptionOut: &fmt)
        guard let fmt else { return }
        var timing = CMSampleTimingInfo(duration: .invalid,
                                        presentationTimeStamp: CMClockGetTime(CMClockGetHostTimeClock()),
                                        decodeTimeStamp: .invalid)
        var sb: CMSampleBuffer?
        CMSampleBufferCreateReadyWithImageBuffer(allocator: nil, imageBuffer: pb, formatDescription: fmt,
                                                 sampleTiming: &timing, sampleBufferOut: &sb)
        guard let sb else { return }
        if let atts = CMSampleBufferGetSampleAttachmentsArray(sb, createIfNecessary: true), CFArrayGetCount(atts) > 0 {
            let dict = unsafeBitCast(CFArrayGetValueAtIndex(atts, 0), to: CFMutableDictionary.self)
            CFDictionarySetValue(dict,
                                 Unmanaged.passUnretained(kCMSampleAttachmentKey_DisplayImmediately).toOpaque(),
                                 Unmanaged.passUnretained(kCFBooleanTrue).toOpaque())
        }
        if displayLayer.status == .failed { displayLayer.flush() }
        displayLayer.enqueue(sb)
        lastEnqueue = Date()
    }
}

extension HintPiP: AVPictureInPictureSampleBufferPlaybackDelegate {
    func pictureInPictureController(_ c: AVPictureInPictureController, setPlaying playing: Bool) {}
    func pictureInPictureControllerTimeRangeForPlayback(_ c: AVPictureInPictureController) -> CMTimeRange {
        CMTimeRange(start: .negativeInfinity, duration: .positiveInfinity)  // 直播：没有进度条
    }
    func pictureInPictureControllerIsPlaybackPaused(_ c: AVPictureInPictureController) -> Bool { false }
    func pictureInPictureController(_ c: AVPictureInPictureController, didTransitionToRenderSize newRenderSize: CMVideoDimensions) {}
    func pictureInPictureController(_ c: AVPictureInPictureController, skipByInterval skipInterval: CMTime,
                                    completion completionHandler: @escaping () -> Void) { completionHandler() }
}

extension HintPiP: AVPictureInPictureControllerDelegate {
    func pictureInPictureControllerDidStartPictureInPicture(_ c: AVPictureInPictureController) {
        DispatchQueue.main.async { self.pipActive = true }
    }
    func pictureInPictureControllerDidStopPictureInPicture(_ c: AVPictureInPictureController) {
        DispatchQueue.main.async { self.pipActive = false }
    }
}

// MARK: - 把提示画成一张图

enum HintRenderer {
    static let glyph: [Character: String] = [
        "K": "帅", "A": "仕", "B": "相", "N": "马", "R": "车", "C": "炮", "P": "兵",
        "k": "将", "a": "士", "b": "象", "n": "馬", "r": "車", "c": "砲", "p": "卒",
    ]

    static func pixelBuffer(for h: HintData, size: CGSize) -> CVPixelBuffer? {
        let w = Int(size.width), hgt = Int(size.height)
        let attrs: [CFString: Any] = [
            kCVPixelBufferIOSurfacePropertiesKey: [:] as [String: Any],
            kCVPixelBufferCGImageCompatibilityKey: true,
            kCVPixelBufferCGBitmapContextCompatibilityKey: true,
        ]
        var pbOut: CVPixelBuffer?
        guard CVPixelBufferCreate(kCFAllocatorDefault, w, hgt, kCVPixelFormatType_32BGRA,
                                  attrs as CFDictionary, &pbOut) == kCVReturnSuccess, let pb = pbOut else { return nil }
        CVPixelBufferLockBaseAddress(pb, [])
        defer { CVPixelBufferUnlockBaseAddress(pb, []) }
        guard let ctx = CGContext(data: CVPixelBufferGetBaseAddress(pb), width: w, height: hgt, bitsPerComponent: 8,
                                  bytesPerRow: CVPixelBufferGetBytesPerRow(pb), space: CGColorSpaceCreateDeviceRGB(),
                                  bitmapInfo: CGImageAlphaInfo.premultipliedFirst.rawValue | CGBitmapInfo.byteOrder32Little.rawValue)
        else { return nil }
        ctx.translateBy(x: 0, y: CGFloat(hgt))
        ctx.scaleBy(x: 1, y: -1)
        UIGraphicsPushContext(ctx)
        draw(h, size: size)
        UIGraphicsPopContext()
        return pb
    }

    static func draw(_ h: HintData, size: CGSize) {
        let bg = UIColor(red: 0.12, green: 0.16, blue: 0.15, alpha: 1)
        bg.setFill()
        UIRectFill(CGRect(origin: .zero, size: size))

        // ---- 顶部文字 ----
        let stale = (h.frameAgeS ?? 99) > 5
        var title = h.text.isEmpty ? "—" : h.text
        var color = UIColor(red: 0.45, green: 0.85, blue: 0.6, alpha: 1)
        switch h.status {
        case "your_turn": break
        case "opponent_turn": color = .lightGray
        case "thinking": color = UIColor(red: 0.95, green: 0.8, blue: 0.35, alpha: 1)
        case "offline": title = "连不上电脑"; color = UIColor(red: 0.95, green: 0.45, blue: 0.4, alpha: 1)
        default: color = .lightGray
        }
        if stale && h.status != "offline" { title = "画面中断，请开启采集"; color = UIColor(red: 0.95, green: 0.45, blue: 0.4, alpha: 1) }
        drawCentered(title, in: CGRect(x: 8, y: 14, width: size.width - 16, height: 44),
                     font: .systemFont(ofSize: 30, weight: .bold), color: color)
        if h.pv.count > 1 && h.status == "your_turn" {
            drawCentered("→ " + h.pv.dropFirst().joined(separator: " "), in: CGRect(x: 8, y: 62, width: size.width - 16, height: 26),
                         font: .systemFont(ofSize: 17), color: .lightGray)
        }

        // ---- 棋盘 ----
        let cell: CGFloat = 36, x0: CGFloat = 36, y0: CGFloat = 116
        let boardRect = CGRect(x: x0 - 22, y: y0 - 22, width: cell * 8 + 44, height: cell * 9 + 44)
        UIColor(red: 0.87, green: 0.75, blue: 0.55, alpha: 1).setFill()
        UIBezierPath(roundedRect: boardRect, cornerRadius: 8).fill()

        let line = UIBezierPath()
        for r in 0..<10 {
            line.move(to: CGPoint(x: x0, y: y0 + CGFloat(r) * cell))
            line.addLine(to: CGPoint(x: x0 + 8 * cell, y: y0 + CGFloat(r) * cell))
        }
        for c in 0..<9 {
            let x = x0 + CGFloat(c) * cell
            if c == 0 || c == 8 {
                line.move(to: CGPoint(x: x, y: y0)); line.addLine(to: CGPoint(x: x, y: y0 + 9 * cell))
            } else {
                line.move(to: CGPoint(x: x, y: y0)); line.addLine(to: CGPoint(x: x, y: y0 + 4 * cell))
                line.move(to: CGPoint(x: x, y: y0 + 5 * cell)); line.addLine(to: CGPoint(x: x, y: y0 + 9 * cell))
            }
        }
        for top in [CGFloat(0), 7] {  // 九宫斜线
            line.move(to: CGPoint(x: x0 + 3 * cell, y: y0 + top * cell)); line.addLine(to: CGPoint(x: x0 + 5 * cell, y: y0 + (top + 2) * cell))
            line.move(to: CGPoint(x: x0 + 5 * cell, y: y0 + top * cell)); line.addLine(to: CGPoint(x: x0 + 3 * cell, y: y0 + (top + 2) * cell))
        }
        UIColor(white: 0.25, alpha: 1).setStroke()
        line.lineWidth = 1
        line.stroke()

        // ---- 棋子 ----
        let rows = h.board.map { Array($0) }
        for (r, row) in rows.enumerated() where r < 10 {
            for (c, ch) in row.enumerated() where c < 9 {
                guard let g = glyph[ch] else { continue }
                let center = CGPoint(x: x0 + CGFloat(c) * cell, y: y0 + CGFloat(r) * cell)
                let disc = UIBezierPath(arcCenter: center, radius: 15, startAngle: 0, endAngle: .pi * 2, clockwise: true)
                UIColor(red: 0.98, green: 0.93, blue: 0.82, alpha: 1).setFill()
                disc.fill()
                let isRed = ch.isUppercase
                (isRed ? UIColor(red: 0.75, green: 0.1, blue: 0.1, alpha: 1) : UIColor(white: 0.15, alpha: 1)).setStroke()
                disc.lineWidth = 1.5
                disc.stroke()
                drawCentered(g, in: CGRect(x: center.x - 15, y: center.y - 11, width: 30, height: 22),
                             font: .systemFont(ofSize: 17, weight: .bold),
                             color: isRed ? UIColor(red: 0.75, green: 0.1, blue: 0.1, alpha: 1) : UIColor(white: 0.1, alpha: 1))
            }
        }

        // ---- 建议走法箭头 ----
        if h.status == "your_turn", h.arrow.count == 4, !stale {
            let p0 = CGPoint(x: x0 + CGFloat(h.arrow[1]) * cell, y: y0 + CGFloat(h.arrow[0]) * cell)
            let p1 = CGPoint(x: x0 + CGFloat(h.arrow[3]) * cell, y: y0 + CGFloat(h.arrow[2]) * cell)
            let green = UIColor(red: 0.1, green: 0.65, blue: 0.3, alpha: 0.9)
            let ring = UIBezierPath(arcCenter: p0, radius: 17, startAngle: 0, endAngle: .pi * 2, clockwise: true)
            green.setStroke(); ring.lineWidth = 3; ring.stroke()
            let ang = atan2(p1.y - p0.y, p1.x - p0.x)
            let tip = CGPoint(x: p1.x - cos(ang) * 4, y: p1.y - sin(ang) * 4)
            let shaft = UIBezierPath()
            shaft.move(to: CGPoint(x: p0.x + cos(ang) * 16, y: p0.y + sin(ang) * 16))
            shaft.addLine(to: CGPoint(x: tip.x - cos(ang) * 12, y: tip.y - sin(ang) * 12))
            shaft.lineWidth = 6; shaft.lineCapStyle = .round; shaft.stroke()
            let head = UIBezierPath()
            head.move(to: tip)
            head.addLine(to: CGPoint(x: tip.x - cos(ang - 0.45) * 18, y: tip.y - sin(ang - 0.45) * 18))
            head.addLine(to: CGPoint(x: tip.x - cos(ang + 0.45) * 18, y: tip.y - sin(ang + 0.45) * 18))
            head.close()
            green.setFill(); head.fill()
        }
    }

    private static func drawCentered(_ s: String, in rect: CGRect, font: UIFont, color: UIColor) {
        let p = NSMutableParagraphStyle()
        p.alignment = .center
        p.lineBreakMode = .byTruncatingTail
        (s as NSString).draw(in: rect, withAttributes: [.font: font, .foregroundColor: color, .paragraphStyle: p])
    }
}

// MARK: - 在 App 界面里显示同一个画面（画中画要求图层在界面上）

struct PiPPreview: UIViewRepresentable {
    let layer: AVSampleBufferDisplayLayer

    final class Host: UIView {
        var hosted: CALayer?
        override func layoutSubviews() {
            super.layoutSubviews()
            hosted?.frame = bounds
        }
    }

    func makeUIView(context: Context) -> Host {
        let v = Host()
        v.backgroundColor = .black
        v.layer.addSublayer(layer)
        v.hosted = layer
        v.clipsToBounds = true
        v.layer.cornerRadius = 10
        return v
    }

    func updateUIView(_ uiView: Host, context: Context) {}
}
