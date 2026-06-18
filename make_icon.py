#!/usr/bin/env python3
"""Render the Tell Me! app icon (a white microphone on an indigo squircle) into
the .iconset directory given as argv[1]. Run by build_app.sh via the venv python;
build_app.sh then turns the iconset into AppIcon.icns with iconutil."""
import os
import sys

from AppKit import (
    NSBitmapImageRep,
    NSGraphicsContext,
    NSColor,
    NSBezierPath,
    NSMakeRect,
    NSMakePoint,
    NSDeviceRGBColorSpace,
    NSBitmapImageFileTypePNG,
    NSLineCapStyleRound,
)

ICONSET = sys.argv[1]

# pixel size -> iconset filenames that need that resolution
NAMES = {
    16: ["icon_16x16.png"],
    32: ["icon_16x16@2x.png", "icon_32x32.png"],
    64: ["icon_32x32@2x.png"],
    128: ["icon_128x128.png"],
    256: ["icon_128x128@2x.png", "icon_256x256.png"],
    512: ["icon_256x256@2x.png", "icon_512x512.png"],
    1024: ["icon_512x512@2x.png"],
}


def draw(px):
    rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, px, px, 8, 4, True, False, NSDeviceRGBColorSpace, 0, 0
    )
    ctx = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(ctx)

    # Indigo squircle background.
    m = px * 0.06
    side = px - 2 * m
    bg = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(m, m, side, side), side * 0.2237, side * 0.2237
    )
    NSColor.colorWithCalibratedRed_green_blue_alpha_(0.36, 0.31, 0.92, 1.0).set()
    bg.fill()

    # White microphone.
    NSColor.whiteColor().set()
    cx = px / 2.0
    body_w, body_h, body_bottom = px * 0.20, px * 0.34, px * 0.44
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(cx - body_w / 2, body_bottom, body_w, body_h), body_w / 2, body_w / 2
    ).fill()

    lw = px * 0.035
    cradle_c = NSMakePoint(cx, body_bottom + px * 0.03)
    cradle_r = px * 0.145
    cradle = NSBezierPath.bezierPath()
    cradle.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_(cradle_c, cradle_r, 200, 340)
    stand = NSBezierPath.bezierPath()
    stand.moveToPoint_(NSMakePoint(cx, cradle_c.y - cradle_r))
    stand.lineToPoint_(NSMakePoint(cx, px * 0.18))
    base = NSBezierPath.bezierPath()
    base.moveToPoint_(NSMakePoint(cx - px * 0.10, px * 0.18))
    base.lineToPoint_(NSMakePoint(cx + px * 0.10, px * 0.18))
    for path in (cradle, stand, base):
        path.setLineWidth_(lw)
        path.setLineCapStyle_(NSLineCapStyleRound)
        path.stroke()

    NSGraphicsContext.restoreGraphicsState()
    return rep


os.makedirs(ICONSET, exist_ok=True)
for px, names in NAMES.items():
    png = draw(px).representationUsingType_properties_(NSBitmapImageFileTypePNG, {})
    for name in names:
        png.writeToFile_atomically_(os.path.join(ICONSET, name), True)
print("icon rendered")
