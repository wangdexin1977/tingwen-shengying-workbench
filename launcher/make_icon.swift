import AppKit

// 代码原生蓝色图标：小尺寸也能辨认的音波 + 播放符号，不依赖云端生图。
let destination = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
try FileManager.default.createDirectory(at: destination, withIntermediateDirectories: true)
let entries: [(String, Int)] = [
    ("icon_16x16.png", 16), ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32), ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128), ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256), ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512), ("icon_512x512@2x.png", 1024)
]
for (name, size) in entries {
    let image = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size,
        bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
        colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    let context = NSGraphicsContext(bitmapImageRep: image)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = context
    context.cgContext.scaleBy(x: CGFloat(size) / 1024, y: CGFloat(size) / 1024)
    let tile = NSBezierPath(roundedRect: NSRect(x: 56, y: 56, width: 912, height: 912), xRadius: 206, yRadius: 206)
    let gradient = NSGradient(colors: [NSColor(red: 0.24, green: 0.12, blue: 0.66, alpha: 1),
                                     NSColor(red: 0.04, green: 0.65, blue: 0.78, alpha: 1)])!
    gradient.draw(in: tile, angle: 48)
    NSColor(red: 0.26, green: 0.78, blue: 1, alpha: 0.75).setStroke()
    tile.lineWidth = 12
    tile.stroke()
    NSColor.white.setFill()
    for (i, height) in [150.0, 270.0, 410.0, 270.0].enumerated() {
        NSBezierPath(roundedRect: NSRect(x: 214 + Double(i) * 73, y: (1024 - height) / 2,
                                        width: 44, height: height), xRadius: 22, yRadius: 22).fill()
    }
    NSColor(red: 1, green: 0.83, blue: 0.25, alpha: 1).setFill()
    let play = NSBezierPath()
    play.move(to: NSPoint(x: 568, y: 332))
    play.line(to: NSPoint(x: 808, y: 512))
    play.line(to: NSPoint(x: 568, y: 692))
    play.close()
    play.fill()
    NSGraphicsContext.restoreGraphicsState()
    try image.representation(using: .png, properties: [:])!.write(to: destination.appendingPathComponent(name))
}
