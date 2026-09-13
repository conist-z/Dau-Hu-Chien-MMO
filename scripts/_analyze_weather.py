from PIL import Image
from pathlib import Path

p = Path.cwd() / "assets" / "gui" / "weather" / "sun_clouds" / "frame_001.png"
im = Image.open(p)
print("size", im.size, "mode", im.mode)
if im.mode in ("RGBA", "LA"):
    r, g, b, a = im.split()
    print("alpha extrema", a.getextrema())
    px = im.load()
    W, H = im.size
    # corner / edge sample
    for (x, y) in [(0,0),(W-1,0),(0,H-1),(W-1,H-1),(8,0),(0,8)]:
        print("edge", (x,y), px[x,y])
    # count near-black opaque pixels
    nb = sum(1 for yy in range(H) for xx in range(W)
             if px[xx,yy][3] > 128 and px[xx,yy][0] < 40 and px[xx,yy][1] < 40 and px[xx,yy][2] < 40)
    print("near-black opaque px:", nb)
    # bounding box of non-transparent
    print("bbox non-transparent:", im.getbbox())
    # bounding box of non-black (treat black as bg)
    bw = im.convert("RGB")
    # make black transparent-ish: create mask of non-near-black
    mask = Image.new("L", (W,H), 0)
    mp = mask.load()
    bp = bw.load()
    for yy in range(H):
        for xx in range(W):
            rr,gg,bb = bp[xx,yy]
            if not (rr<40 and gg<40 and bb<40):
                mp[xx,yy]=255
    print("bbox non-black:", mask.getbbox())
else:
    print("mode not RGBA", im.mode)
