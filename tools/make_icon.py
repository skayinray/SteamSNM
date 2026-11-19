"""Генерация иконки SteamSNM (нужен только при сборке)."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "src" / "resources" / "icon.png"
OUT.parent.mkdir(parents=True, exist_ok=True)

SIZE = 256
image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
draw = ImageDraw.Draw(image)

# Фон: скруглённый квадрат с градиентом в стиле Steam.
mask = Image.new("L", (SIZE, SIZE), 0)
ImageDraw.Draw(mask).rounded_rectangle((0, 0, SIZE - 1, SIZE - 1), radius=48, fill=255)
background = Image.new("RGBA", (SIZE, SIZE))
bg_draw = ImageDraw.Draw(background)
for y in range(SIZE):
    t = y / SIZE
    color = (
        int(27 + (42 - 27) * t),
        int(40 + (71 - 40) * t),
        int(56 + (94 - 56) * t),
        255,
    )
    bg_draw.line((0, y, SIZE, y), fill=color)
image.paste(background, (0, 0), mask)

# Свечение и рамка.
draw.rounded_rectangle((5, 5, SIZE - 6, SIZE - 6), radius=44, outline=(102, 192, 244, 90), width=3)

# График цены, идущий вниз + точка.
points = [(38, 78), (78, 96), (112, 82), (146, 128), (182, 118), (218, 162)]
draw.line(points, fill=(102, 192, 244, 255), width=11, joint="curve")
for x, y in points:
    draw.ellipse((x - 7, y - 7, x + 7, y + 7), fill=(102, 192, 244, 255))

# Стрелка снижения цены.
draw.polygon([(218, 142), (238, 142), (228, 174)], fill=(164, 208, 7, 255))
draw.rounded_rectangle((207, 132, 229, 148), radius=5, fill=(164, 208, 7, 255))

font_path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
font = ImageFont.truetype(str(font_path), 52) if font_path.exists() else ImageFont.load_default()
draw.text((128, 198), "SNM", font=font, fill=(255, 255, 255, 255), anchor="mm")

image.save(OUT)
print(f"Иконка сохранена: {OUT}")
