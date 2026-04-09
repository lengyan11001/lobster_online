#!/usr/bin/swift
import AppKit
import CoreGraphics

guard CommandLine.arguments.count >= 4 else {
    FileHandle.standardError.write(Data("usage: replace_icon_bg.swift <in.png> <out.png> <#RRGGBB>\n".utf8))
    exit(1)
}
let inPath = CommandLine.arguments[1]
let outPath = CommandLine.arguments[2]
let hex = CommandLine.arguments[3].trimmingCharacters(in: CharacterSet(charactersIn: "#"))
guard hex.count == 6,
      let br = UInt8(hex.prefix(2), radix: 16),
      let bg = UInt8(hex.dropFirst(2).prefix(2), radix: 16),
      let bb = UInt8(hex.suffix(2), radix: 16) else {
    exit(1)
}

guard let nsimg = NSImage(contentsOfFile: inPath),
      let cg = nsimg.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    FileHandle.standardError.write(Data("load fail\n".utf8))
    exit(1)
}
let w = cg.width
let h = cg.height
var data = [UInt8](repeating: 0, count: w * h * 4)
guard let space = CGColorSpace(name: CGColorSpace.sRGB),
      let ctx = CGContext(
        data: &data,
        width: w,
        height: h,
        bitsPerComponent: 8,
        bytesPerRow: w * 4,
        space: space,
        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
      ) else {
    exit(1)
}
ctx.draw(cg, in: CGRect(x: 0, y: 0, width: w, height: h))

// 四角平均为背景参考
var sr = 0, sg = 0, sb = 0, nn = 0
let samples = [(2, 2), (w - 3, 2), (2, h - 3), (w - 3, h - 3)]
for (sx, sy) in samples {
    let o = (sy * w + sx) * 4
    sr += Int(data[o])
    sg += Int(data[o + 1])
    sb += Int(data[o + 2])
    nn += 1
}
let ar = sr / nn, ag = sg / nn, ab = sb / nn

func dist2(_ r: Int, _ g: Int, _ b: Int) -> Int {
    let dr = r - ar, dg = g - ag, db = b - ab
    return dr * dr + dg * dg + db * db
}

let thresh = 55 * 55 * 3
for y in 0..<h {
    for x in 0..<w {
        let o = (y * w + x) * 4
        let r = Int(data[o]), g = Int(data[o + 1]), b = Int(data[o + 2])
        let a = Int(data[o + 3])
        let dd = dist2(r, g, b)
        let mx = max(r, max(g, b)), mn = min(r, min(g, b))
        let sat = mx - mn
        let isGrayish = sat < 38 && r >= 25 && r <= 140 && g >= 25 && g <= 140 && b >= 25 && b <= 140
        let isNearCorner = dd < thresh
        if a < 15 {
            data[o] = br
            data[o + 1] = bg
            data[o + 2] = bb
            data[o + 3] = 0
        } else if isNearCorner || (isGrayish && dd < 80 * 80 * 3) {
            data[o] = br
            data[o + 1] = bg
            data[o + 2] = bb
            data[o + 3] = UInt8(a)
        }
    }
}

guard let outCtx = CGContext(
    data: &data,
    width: w,
    height: h,
    bitsPerComponent: 8,
    bytesPerRow: w * 4,
    space: space,
    bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
),
      let outCg = outCtx.makeImage() else {
    exit(1)
}
let outImg = NSImage(cgImage: outCg, size: NSSize(width: w, height: h))
guard let tiff = outImg.tiffRepresentation,
      let rep = NSBitmapImageRep(data: tiff),
      let pngData = rep.representation(using: .png, properties: [:]) else {
    exit(1)
}
try pngData.write(to: URL(fileURLWithPath: outPath))
print("wrote \(outPath)")
