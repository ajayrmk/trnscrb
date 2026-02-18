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

    S = 44              # canvas size (retina menu bar = 22 pt @ 2x)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx = S // 2

    # ── mic capsule body ──────────────────────────────────────────────────────
    bw, bh = 14, 22     # body width / height
    bx = cx - bw // 2
    by = 4
    d.rounded_rectangle([bx, by, bx + bw, by + bh], radius=bw // 2, fill=fill)

    # ── stand arc (open bottom semicircle) ────────────────────────────────────
    ar = 11             # arc radius
    acy = by + bh - 4   # arc centre y
    lw = 2
    d.arc(
        [cx - ar, acy - ar, cx + ar, acy + ar],
        start=180, end=0,
        fill=fill, width=lw,
    )

    # ── vertical stem ─────────────────────────────────────────────────────────
    stem_top = acy + ar - 1
    stem_bot = S - 8
    d.line([cx, stem_top, cx, stem_bot], fill=fill, width=lw)

    # ── base bar ─────────────────────────────────────────────────────────────
    bbar = 10
    d.line([cx - bbar, stem_bot, cx + bbar, stem_bot], fill=fill, width=lw)

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
