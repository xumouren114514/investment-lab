"""Generate a small original chart icon with standard-library ICO/DIB encoding."""
from pathlib import Path
import struct


def build_icon(path):
    images = []
    for size in (16, 32, 48, 64):
        pixels = bytearray()
        for y in range(size - 1, -1, -1):
            for x in range(size):
                px, py = x / size, y / size
                color = (23, 43, 62, 255)
                for left, top in ((.18, .57), (.40, .39), (.62, .20)):
                    if left <= px < left + .16 and top <= py < .80:
                        color = (60, 193, 174, 255)
                if .14 <= px <= .84 and .85 <= py <= .88:
                    color = (205, 225, 232, 255)
                r, g, b, a = color
                pixels.extend((b, g, r, a))
        mask = bytes(((size + 31) // 32) * 4 * size)
        dib = struct.pack('<IIIHHIIIIII', 40, size, size * 2, 1, 32, 0, len(pixels), 0, 0, 0, 0)
        images.append((size, dib + pixels + mask))
    offset = 6 + 16 * len(images)
    directory = bytearray(struct.pack('<HHH', 0, 1, len(images)))
    for size, payload in images:
        directory.extend(struct.pack('<BBBBHHII', size, size, 0, 0, 1, 32, len(payload), offset))
        offset += len(payload)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(directory + b''.join(payload for _, payload in images))


if __name__ == '__main__':
    build_icon(Path(__file__).parent / 'assets/investment-lab.ico')
