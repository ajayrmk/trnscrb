"""Generate the Trnscrb menu bar icon (mic silhouette PNG).

Run once after install:
    python -m trnscrb.icon

Saves a 44x44 template PNG to ~/.local/share/trnscrb/mic.png
"""
from pathlib import Path


ICON_DIR = Path.home() / ".local" / "share" / "trnscrb"
ICON_IDLE = ICON_DIR / "mic.png"
ICON_RECORDING = ICON_DIR / "mic_active.png"


def generate_icons() -> None:
    from PIL import Image, ImageDraw

    ICON_DIR.mkdir(parents=True, exist_ok=True)

    _make_mic(ICON_IDLE, fill=(0, 0, 0, 255))         # black  — idle (macOS template image)
    _make_mic(ICON_RECORDING, fill=(220, 38, 38, 255)) # red    — recording


def _make_mic(path: Path, fill: tuple) -> None:
    from PIL import Image, ImageDraw

    S = 44  # canvas size (retina menu bar = 22 pt @ 2x)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # ── waveform bars — mirrors the trnscrb logo icon ─────────────────────────
    # Logo bar heights (relative): 32, 52, 72, 48, 24  (tallest = 72)
    # Scale tallest bar to 34px to fill 44px canvas with 5px top/bottom margin
    scale  = 34 / 72
    bar_w  = 4
    gap    = 3
    total_w = 5 * bar_w + 4 * gap  # = 32px
    x0     = (S - total_w) // 2    # left edge, centred
    cy     = S // 2

    heights   = [int(h * scale) for h in [32, 52, 72, 48, 24]]
    opacities = [0.45, 0.72, 1.0, 0.72, 0.45]

    r, g, b, a = fill
    for i, (h, op) in enumerate(zip(heights, opacities)):
        x  = x0 + i * (bar_w + gap)
        y0 = cy - h // 2
        y1 = cy + h // 2
        color = (r, g, b, int(a * op))
        d.rounded_rectangle([x, y0, x + bar_w, y1], radius=bar_w // 2, fill=color)

    img.save(str(path))


def icon_path(recording: bool = False) -> str | None:
    """Return path to icon PNG if it exists, else None (falls back to emoji)."""
    p = ICON_RECORDING if recording else ICON_IDLE
    return str(p) if p.exists() else None


if __name__ == "__main__":
    generate_icons()
    print(f"Icons written to {ICON_DIR}")


def generate_icons_cli() -> None:
    """Entry point called from the trnscrb CLI (uses the uv tool's Python with PIL)."""
    try:
        generate_icons()
        print(f"✓ Icons written to {ICON_DIR}")
    except ImportError:
        print("Pillow not available — menu bar will use emoji fallback (🎙 / 🔴). That's fine.")
    except Exception as e:
        print(f"Icon generation failed: {e}")
