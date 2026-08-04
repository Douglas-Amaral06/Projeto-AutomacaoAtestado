from pathlib import Path

from app.main import detected_mime


def test_magic_byte_detection(tmp_path: Path):
    samples = {
        "a.pdf": (b"%PDF-1.7\n", "application/pdf"),
        "a.jpg": (b"\xff\xd8\xff\xe0test", "image/jpeg"),
        "a.png": (b"\x89PNG\r\n\x1a\nrest", "image/png"),
        "a.webp": (b"RIFF0000WEBPrest", "image/webp"),
    }
    for name, (payload, expected) in samples.items():
        path = tmp_path / name
        path.write_bytes(payload)
        assert detected_mime(path) == expected
