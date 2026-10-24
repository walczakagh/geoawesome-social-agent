from pathlib import Path
from PIL import Image
from agent import images
u = "https://i.redd.it/c57pc8avl9xh1.png"
im = images.download(u)
out = Path("tools/probe_results"); out.mkdir(parents=True, exist_ok=True)
sq, px = images.make_square([im], [""])
sq.save(out / "crop.jpg", quality=90)
# whole map fitted into the square on the image's own corner colour
bg = im.getpixel((5, 5))
w, h = im.size
s = 1080 / max(w, h)
small = im.resize((round(w * s), round(h * s)), Image.LANCZOS)
canvas = Image.new("RGB", (1080, 1080), bg)
canvas.paste(small, ((1080 - small.width) // 2, (1080 - small.height) // 2))
canvas.save(out / "fit.jpg", quality=92)
print("size", im.size, "bg", bg, "crop src px", px)
