"""
Vector-quality multi-resolution icon generator for Game Save Guardian.
Generates all required icon formats and resolutions for:
- Windows Desktop Executable & Explorer (.ico: 256, 128, 64, 48, 32, 24, 16)
- High-res PNG assets (512x512, 256x256, 128x128, 64x64, 48x48, 32x32, 24x24, 16x16)
- System Tray Minimized mode icons (Active Emerald & Paused Amber states)
"""

import os
from pathlib import Path

# Ensure offscreen Qt execution
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PIL import Image
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)


def render_master_icon(size: int = 512, is_paused: bool = False, is_tray_tuned: bool = False) -> Image.Image:
    """Render high-fidelity vector shield & gamepad icon at specified resolution."""
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)

    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)

    scale = size / 512.0

    # Color definitions matching Game Save Guardian design system
    if is_paused:
        c_primary = QColor("#f59e0b")      # Amber 500
        c_secondary = QColor("#fbbf24")    # Amber 400
        c_inner1 = QColor("#78350f")       # Amber 900
        c_inner2 = QColor("#b45309")       # Amber 700
    else:
        c_primary = QColor("#10b981")      # Emerald 500
        c_secondary = QColor("#38bdf8")    # Sky Cyan 400
        c_inner1 = QColor("#064e3b")       # Emerald 900
        c_inner2 = QColor("#047857")       # Emerald 700

    c_shield_bg1 = QColor("#1e293b")       # Slate 800
    c_shield_bg2 = QColor("#090d16")       # Dark Obsidian

    # 1. Outer Radial Glow
    radial_glow = QRadialGradient(256 * scale, 240 * scale, 230 * scale)
    glow_col = QColor(c_primary)
    glow_col.setAlpha(70 if not is_tray_tuned else 90)
    radial_glow.setColorAt(0.0, glow_col)
    glow_trans = QColor(c_primary)
    glow_trans.setAlpha(0)
    radial_glow.setColorAt(1.0, glow_trans)
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(radial_glow))
    p.drawEllipse(QRectF(20 * scale, 15 * scale, 472 * scale, 472 * scale))

    # 2. Outer Shield Path (Aerodynamic modern tech shield)
    shield_path = QPainterPath()
    shield_path.moveTo(256 * scale, 44 * scale)
    shield_path.lineTo(390 * scale, 76 * scale)
    shield_path.quadTo(448 * scale, 170 * scale, 428 * scale, 260 * scale)
    shield_path.quadTo(395 * scale, 370 * scale, 256 * scale, 470 * scale)
    shield_path.quadTo(117 * scale, 370 * scale, 84 * scale, 260 * scale)
    shield_path.quadTo(64 * scale, 170 * scale, 122 * scale, 76 * scale)
    shield_path.closeSubpath()

    # Outer Brushed Titanium Rim
    rim_grad = QLinearGradient(0, 40 * scale, 0, 480 * scale)
    rim_grad.setColorAt(0.0, QColor("#64748b"))
    rim_grad.setColorAt(0.5, QColor("#334155"))
    rim_grad.setColorAt(1.0, QColor("#0f172a"))
    p.setPen(QPen(QColor("#0f172a"), max(1.0, 3 * scale)))
    p.setBrush(QBrush(rim_grad))
    p.drawPath(shield_path)

    # 3. Inner Shield Face
    inner_path = QPainterPath()
    inner_path.moveTo(256 * scale, 62 * scale)
    inner_path.lineTo(374 * scale, 90 * scale)
    inner_path.quadTo(424 * scale, 175 * scale, 408 * scale, 255 * scale)
    inner_path.quadTo(376 * scale, 355 * scale, 256 * scale, 448 * scale)
    inner_path.quadTo(136 * scale, 355 * scale, 104 * scale, 255 * scale)
    inner_path.quadTo(88 * scale, 175 * scale, 138 * scale, 90 * scale)
    inner_path.closeSubpath()

    face_grad = QRadialGradient(256 * scale, 180 * scale, 280 * scale)
    face_grad.setColorAt(0.0, c_shield_bg1)
    face_grad.setColorAt(1.0, c_shield_bg2)
    p.setPen(QPen(c_primary, max(1.0, 4.0 * scale)))
    p.setBrush(QBrush(face_grad))
    p.drawPath(inner_path)

    # 4. Inner Neon Geometric Accent Line
    if not is_tray_tuned or size >= 64:
        accent_path = QPainterPath()
        accent_path.moveTo(256 * scale, 80 * scale)
        accent_path.lineTo(356 * scale, 104 * scale)
        accent_path.quadTo(398 * scale, 180 * scale, 386 * scale, 248 * scale)
        accent_path.quadTo(356 * scale, 335 * scale, 256 * scale, 420 * scale)
        accent_path.quadTo(156 * scale, 335 * scale, 126 * scale, 248 * scale)
        accent_path.quadTo(114 * scale, 180 * scale, 156 * scale, 104 * scale)
        accent_path.closeSubpath()
        accent_pen = QPen(c_primary, max(1.0, 1.5 * scale))
        accent_pen.setStyle(Qt.DashLine)
        p.setPen(accent_pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(accent_path)

    # 5. Gaming Controller Silhouette Emblem
    ctrl_path = QPainterPath()
    ctrl_path.moveTo(190 * scale, 195 * scale)
    ctrl_path.quadTo(256 * scale, 205 * scale, 322 * scale, 195 * scale) # top curve
    ctrl_path.quadTo(375 * scale, 205 * scale, 385 * scale, 265 * scale) # right shoulder
    ctrl_path.quadTo(395 * scale, 330 * scale, 355 * scale, 355 * scale) # right grip bottom
    ctrl_path.quadTo(325 * scale, 370 * scale, 305 * scale, 310 * scale) # right inner curve
    ctrl_path.quadTo(256 * scale, 330 * scale, 207 * scale, 310 * scale) # center bottom arch
    ctrl_path.quadTo(187 * scale, 370 * scale, 157 * scale, 355 * scale) # left inner curve
    ctrl_path.quadTo(117 * scale, 330 * scale, 127 * scale, 265 * scale) # left grip bottom
    ctrl_path.quadTo(137 * scale, 205 * scale, 190 * scale, 195 * scale) # left shoulder
    ctrl_path.closeSubpath()

    ctrl_grad = QLinearGradient(120 * scale, 190 * scale, 390 * scale, 370 * scale)
    ctrl_grad.setColorAt(0.0, c_inner1)
    ctrl_grad.setColorAt(0.5, c_inner2)
    ctrl_grad.setColorAt(1.0, c_primary)
    p.setPen(QPen(c_secondary, max(1.5, 3.0 * scale)))
    p.setBrush(QBrush(ctrl_grad))
    p.drawPath(ctrl_path)

    # D-Pad on Left Grip
    p.setPen(QPen(c_secondary, max(1.0, 1.5 * scale)))
    p.setBrush(QBrush(QColor("#0f172a")))
    p.drawRoundedRect(QRectF(175 * scale, 240 * scale, 18 * scale, 46 * scale), 3 * scale, 3 * scale)
    p.drawRoundedRect(QRectF(161 * scale, 254 * scale, 46 * scale, 18 * scale), 3 * scale, 3 * scale)

    # Action Buttons on Right Grip
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(c_secondary))
    p.drawEllipse(QRectF(330 * scale, 242 * scale, 12 * scale, 12 * scale))
    p.drawEllipse(QRectF(348 * scale, 260 * scale, 12 * scale, 12 * scale))
    p.drawEllipse(QRectF(330 * scale, 278 * scale, 12 * scale, 12 * scale))
    p.drawEllipse(QRectF(312 * scale, 260 * scale, 12 * scale, 12 * scale))

    # Dual Thumbsticks
    p.setPen(QPen(c_primary, max(1.0, 1.8 * scale)))
    p.setBrush(QBrush(QColor("#0f172a")))
    p.drawEllipse(QRectF(205 * scale, 275 * scale, 26 * scale, 26 * scale))
    p.drawEllipse(QRectF(281 * scale, 275 * scale, 26 * scale, 26 * scale))
    p.setBrush(QBrush(c_primary))
    p.setPen(Qt.NoPen)
    p.drawEllipse(QRectF(214 * scale, 284 * scale, 8 * scale, 8 * scale))
    p.drawEllipse(QRectF(290 * scale, 284 * scale, 8 * scale, 8 * scale))

    # Center Floppy Save / Data Vault Badge
    floppy_rect = QRectF(238 * scale, 222 * scale, 36 * scale, 36 * scale)
    p.setPen(QPen(QColor("#ffffff"), max(1.0, 1.8 * scale)))
    p.setBrush(QBrush(QColor("#0f172a")))
    p.drawRoundedRect(floppy_rect, 4 * scale, 4 * scale)

    # Shutter metal slider
    shutter_rect = QRectF(244 * scale, 222 * scale, 24 * scale, 14 * scale)
    p.setBrush(QBrush(c_primary))
    p.drawRoundedRect(shutter_rect, 2 * scale, 2 * scale)

    # Verified Save Shield Checkmark
    check_path = QPainterPath()
    check_path.moveTo(246 * scale, 244 * scale)
    check_path.lineTo(253 * scale, 251 * scale)
    check_path.lineTo(266 * scale, 238 * scale)
    p.setPen(QPen(c_primary, max(1.5, 3.0 * scale), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)
    p.drawPath(check_path)

    # 6. Upper Specular Arc Highlight
    glass_path = QPainterPath()
    glass_path.moveTo(256 * scale, 64 * scale)
    glass_path.lineTo(370 * scale, 92 * scale)
    glass_path.quadTo(414 * scale, 160 * scale, 396 * scale, 210 * scale)
    glass_path.quadTo(256 * scale, 260 * scale, 116 * scale, 210 * scale)
    glass_path.quadTo(98 * scale, 160 * scale, 142 * scale, 92 * scale)
    glass_path.closeSubpath()

    glass_grad = QLinearGradient(256 * scale, 60 * scale, 256 * scale, 240 * scale)
    glass_grad.setColorAt(0.0, QColor(255, 255, 255, 85))
    glass_grad.setColorAt(0.8, QColor(255, 255, 255, 12))
    glass_grad.setColorAt(1.0, QColor(255, 255, 255, 0))
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(glass_grad))
    p.drawPath(glass_path)

    p.end()

    # Convert to PIL Image
    buffer = img.bits().tobytes()
    return Image.frombuffer("RGBA", (size, size), buffer, "raw", "BGRA", 0, 1)


def generate_all_assets() -> None:
    """Generate complete suite of Windows icons and multi-resolution ICOs."""
    assets_dir = Path("assets")
    assets_dir.mkdir(parents=True, exist_ok=True)

    print("Generating Master 512x512 Game Save Guardian Icon...")
    master_512 = render_master_icon(512, is_paused=False)
    master_512.save(assets_dir / "icon_512.png", "PNG")

    # Standard Windows PNG icon resolutions
    resolutions = [256, 128, 64, 48, 32, 24, 16]
    pil_images: list[Image.Image] = []

    for res in resolutions:
        resized = master_512.resize((res, res), Image.Resampling.LANCZOS)
        resized.save(assets_dir / f"icon_{res}.png", "PNG")
        pil_images.append(resized)

    # Master icon.png is 256x256
    pil_images[0].save(assets_dir / "icon.png", "PNG")

    # Save Windows multi-resolution .ico containing all sizes
    ico_path = assets_dir / "icon.ico"
    master_512.save(
        ico_path,
        format="ICO",
        sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)]
    )
    print(f"Generated multi-resolution ICO: {ico_path} ({os.path.getsize(ico_path)} bytes)")

    # Specialized High-Contrast System Tray Icons (Active Emerald & Paused Amber)
    print("Generating High-Contrast Minimized System Tray Icons...")
    tray_active_32 = render_master_icon(64, is_paused=False, is_tray_tuned=True).resize((32, 32), Image.Resampling.LANCZOS)
    tray_active_16 = tray_active_32.resize((16, 16), Image.Resampling.LANCZOS)
    tray_active_32.save(assets_dir / "tray_active_32.png", "PNG")
    tray_active_16.save(assets_dir / "tray_active.png", "PNG")
    tray_active_32.save(
        assets_dir / "tray_active.ico",
        format="ICO",
        sizes=[(32, 32), (24, 24), (16, 16)]
    )

    tray_paused_32 = render_master_icon(64, is_paused=True, is_tray_tuned=True).resize((32, 32), Image.Resampling.LANCZOS)
    tray_paused_16 = tray_paused_32.resize((16, 16), Image.Resampling.LANCZOS)
    tray_paused_32.save(assets_dir / "tray_paused_32.png", "PNG")
    tray_paused_16.save(assets_dir / "tray_paused.png", "PNG")
    tray_paused_32.save(
        assets_dir / "tray_paused.ico",
        format="ICO",
        sizes=[(32, 32), (24, 24), (16, 16)]
    )

    # Clean vector checkbox and radio button indicators
    img_chk = QImage(18, 18, QImage.Format_ARGB32)
    img_chk.fill(Qt.transparent)
    p = QPainter(img_chk)
    p.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor("#ffffff"), 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.drawLine(4, 9, 8, 13)
    p.drawLine(8, 13, 14, 5)
    p.end()
    img_chk.save(str(assets_dir / "check_icon.png"))

    img_rad = QImage(18, 18, QImage.Format_ARGB32)
    img_rad.fill(Qt.transparent)
    p = QPainter(img_rad)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(QColor("#10b981")))
    p.drawEllipse(5, 5, 8, 8)
    p.end()
    img_rad.save(str(assets_dir / "radio_dot.png"))

    print("All icons successfully generated in assets/ directory!")


if __name__ == "__main__":
    generate_all_assets()
