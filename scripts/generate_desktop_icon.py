"""Generate the small application icon without extra build dependencies."""
import math
from pathlib import Path
import struct
import zlib

points = [(17,35),(24,35),(30,20),(37,46),(43,31),(48,31)]
def distance(x, y, a, b):
    dx, dy = b[0]-a[0], b[1]-a[1]
    t = max(0, min(1, ((x-a[0])*dx+(y-a[1])*dy)/(dx*dx+dy*dy)))
    return math.hypot(x-a[0]-t*dx, y-a[1]-t*dy)

raw = bytearray()
for y in range(64):
    raw.append(0)
    for x in range(64):
        corner = math.hypot(max(16-x, x-47, 0), max(16-y, y-47, 0))
        color = (38,132,87,255 if corner <= 16 else 0)
        if min(distance(x,y,a,b) for a,b in zip(points, points[1:])) <= 2:
            color = (255,255,255,255)
        raw.extend(color)
def chunk(name, data):
    return struct.pack('!I',len(data))+name+data+struct.pack('!I',zlib.crc32(name+data))
png = b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',64,64,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b'')
ico = struct.pack('<3H',0,1,1)+struct.pack('<4B2H2I',64,64,0,0,1,32,len(png),22)+png
destination = Path(__file__).resolve().parents[1]/'src/server_network_assist/desktop_ui/icon.ico'
destination.write_bytes(ico)
print(destination)
