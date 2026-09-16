"""Bounded local Office input validation. Never extract or execute archive parts."""
from pathlib import Path, PurePosixPath
import zipfile

MAX_ARCHIVE_ENTRIES = 5000
MAX_ARCHIVE_EXPANDED_BYTES = 128 * 1024 * 1024
MAX_ARCHIVE_PART_BYTES = 32 * 1024 * 1024
MAX_SHEET_CELLS = 200_000


def validate_office_archive(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_ARCHIVE_ENTRIES:
            raise ValueError("Office 文件内部条目过多，请拆分文件。")
        total = 0
        names = set()
        for info in entries:
            name = info.filename.replace("\\", "/")
            parts = PurePosixPath(name)
            if (parts.is_absolute() or ".." in parts.parts or ":" in name or name in names
                    or info.flag_bits & 1):
                raise ValueError("Office 压缩结构存在重复、加密或不安全路径。")
            names.add(name)
            total += info.file_size
            if info.file_size > MAX_ARCHIVE_PART_BYTES or total > MAX_ARCHIVE_EXPANDED_BYTES:
                raise ValueError("Office 解压后超过读取上限，请拆分文件。")
            if info.file_size > 1_000_000 and info.file_size / max(1, info.compress_size) > 1000:
                raise ValueError("Office 压缩比例异常，请提供正常导出的资料。")
            if name.casefold().endswith("vbaproject.bin"):
                raise ValueError("不执行或读取含宏的 Office 文件，请另存为无宏版本。")
            # OOXML never needs a DTD. Reject before downstream XML parsers.
            if name.casefold().endswith((".xml", ".rels")):
                with archive.open(info) as stream:
                    tail = b""
                    while chunk := stream.read(65536):
                        chunk = tail + chunk
                        # Includes UTF-16 declarations without decoding customer content.
                        scan = chunk.replace(b"\x00", b"").upper()
                        if b"<!DOCTYPE" in scan or b"<!ENTITY" in scan:
                            raise ValueError("Office XML 包含不支持的实体声明。")
                        tail = chunk[-32:]
