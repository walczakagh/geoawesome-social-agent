import sys
from agent import images
for u in sys.argv[1:]:
    try:
        im = images.download(u); print("OK", im.size, u)
    except Exception as e:
        print("FAIL", type(e).__name__, str(e)[:150], u)
