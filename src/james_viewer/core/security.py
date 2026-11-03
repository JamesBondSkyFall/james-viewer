"""Opening and re-protecting password-protected PowerPoint files, with the correct password only."""

from __future__ import annotations

import os
import shutil
import struct
import uuid
from pathlib import Path

from .errors import CorruptFile, WrongPassword

_ENCRYPTED_TOKEN = 0xF3D1C4DF
_PLAIN_TOKEN = 0xE391C05F
_USER_EDIT_ATOM = 0x0FF5
_PERSIST_DIRECTORY_ATOM = 0x1772
_CRYPT_SESSION_CONTAINER = 0x2F14


def decrypt_office(path: str | Path, password: str, out_path: str | Path) -> None:
    """Decrypt a PPTX to `out_path`. The password is checked first and is the only one ever tried."""
    import msoffcrypto
    from msoffcrypto import exceptions as office_errors

    path = Path(path)
    try:
        with open(path, "rb") as handle:
            office_file = msoffcrypto.OfficeFile(handle)
            office_file.load_key(password=password, verify_password=True)
            with open(out_path, "wb") as output:
                office_file.decrypt(output)
    except office_errors.InvalidKeyError as exc:
        Path(out_path).unlink(missing_ok=True)
        raise WrongPassword("That password is not correct.") from exc
    except (office_errors.DecryptionError, office_errors.ParseError, office_errors.FileFormatError, ValueError, OSError) as exc:
        Path(out_path).unlink(missing_ok=True)
        message = str(exc)
        if "password" in message.lower() or "key" in message.lower():
            raise WrongPassword("That password is not correct.") from exc
        raise CorruptFile(f"{path.name} could not be decrypted: {message}") from exc


def decrypt_ppt(path: str | Path, password: str, out_path: str | Path) -> None:
    """Decrypt a legacy .ppt, following [MS-PPT] 2.3.7 and [MS-OFFCRYPTO] 2.3.5.

    The office engine cannot open password-protected .ppt files. msoffcrypto verifies the
    password correctly, but its decrypt step damages the document and skips the pictures.
    So it supplies the salt and key size, and the streams are decrypted here.
    """
    import msoffcrypto
    import olefile
    from msoffcrypto import exceptions as office_errors

    path, out_path = Path(path), Path(out_path)
    try:
        with open(path, "rb") as handle:
            office_file = msoffcrypto.OfficeFile(handle)
            office_file.load_key(password=password)
            salt, key_size = office_file.salt, office_file.keySize

        with olefile.OleFileIO(str(path)) as source:
            current_user = bytearray(source.openstream("Current User").read())
            document = bytearray(source.openstream("PowerPoint Document").read())
            pictures = bytearray(source.openstream("Pictures").read()) if source.exists("Pictures") else None

        _decrypt_document(current_user, document, password, salt, key_size)
        if pictures is not None:
            _decrypt_pictures(pictures, password, salt, key_size)

        shutil.copyfile(path, out_path)
        _overwrite_streams(out_path, {"Current User": current_user, "PowerPoint Document": document, "Pictures": pictures})
    except office_errors.InvalidKeyError as exc:
        out_path.unlink(missing_ok=True)
        raise WrongPassword("That password is not correct.") from exc
    except (office_errors.DecryptionError, office_errors.ParseError, office_errors.FileFormatError, ValueError, OSError, KeyError, AssertionError, struct.error, NotImplementedError) as exc:
        out_path.unlink(missing_ok=True)
        raise CorruptFile(f"{path.name} could not be decrypted: {exc}") from exc


def _rc4(password: str, salt: bytes, key_size: int, block: int):
    from cryptography.hazmat.decrepit.ciphers.algorithms import ARC4
    from cryptography.hazmat.primitives.ciphers import Cipher
    from msoffcrypto.method.rc4_cryptoapi import _makekey

    return Cipher(ARC4(_makekey(password, salt, key_size, block)), mode=None).decryptor()


def _decrypt_document(current_user: bytearray, document: bytearray, password: str, salt: bytes, key_size: int) -> None:
    token = struct.unpack_from("<I", current_user, 12)[0]
    if token != _ENCRYPTED_TOKEN:
        raise ValueError("this file is not marked as encrypted")
    newest_edit = struct.unpack_from("<I", current_user, 16)[0]

    # Walk every saved revision, newest first, collecting each persist object it lists.
    objects: dict[int, int] = {}  # offset -> persist id, which keys that object's cipher
    session_id = None
    edit = newest_edit
    seen = set()
    while edit not in seen:
        seen.add(edit)
        record_type, length = struct.unpack_from("<HI", document, edit + 2)
        if record_type != _USER_EDIT_ATOM:
            raise ValueError("the edit history is damaged")
        last_edit, directory = struct.unpack_from("<II", document, edit + 16)
        if edit == newest_edit and length >= 0x20:
            session_id = struct.unpack_from("<I", document, edit + 36)[0]
        record_type, directory_length = struct.unpack_from("<HI", document, directory + 2)
        if record_type != _PERSIST_DIRECTORY_ATOM:
            raise ValueError("the persist directory is damaged")
        position = directory + 8
        end = position + directory_length
        while position < end:
            entry = struct.unpack_from("<I", document, position)[0]
            first_id, count = entry & 0xFFFFF, entry >> 20
            position += 4
            for offset_index in range(count):
                objects.setdefault(struct.unpack_from("<I", document, position)[0], first_id + offset_index)
                position += 4
        if last_edit == 0:
            break
        edit = last_edit

    for offset, persist_id in objects.items():
        if struct.unpack_from("<H", document, offset + 2)[0] == _CRYPT_SESSION_CONTAINER and persist_id == session_id:
            continue  # the one object stored in the clear
        decryptor = _rc4(password, salt, key_size, persist_id)
        header = decryptor.update(bytes(document[offset : offset + 8]))
        length = struct.unpack_from("<I", header, 4)[0]
        stop = min(offset + 8 + length, len(document))
        document[offset : offset + 8] = header
        document[offset + 8 : stop] = decryptor.update(bytes(document[offset + 8 : stop]))

    # Mark the file as not encrypted. The session object stays, but nothing refers to it any more.
    struct.pack_into("<I", current_user, 12, _PLAIN_TOKEN)
    struct.pack_into("<I", document, newest_edit + 4, 0x1C)
    struct.pack_into("<I", document, newest_edit + 36, 0)


_TWO_UID_INSTANCES = {0x217, 0x3D5, 0x46B, 0x543, 0x6E1, 0x6E3, 0x6E5, 0x7A9}


def _decrypt_pictures(data: bytearray, password: str, salt: bytes, key_size: int) -> None:
    """Decrypt the Pictures stream. Every field of a picture record restarts the cipher at block 0."""

    def section(start: int, size: int) -> int:
        stop = min(start + size, len(data))
        data[start:stop] = _rc4(password, salt, key_size, 0).update(bytes(data[start:stop]))
        return stop

    position = 0
    while position + 8 <= len(data):
        record = position
        position = section(position, 8)
        first_word, record_type, length = struct.unpack_from("<HHI", data, record)
        if record_type != 0xF007 and not 0xF018 <= record_type <= 0xF117:
            # Past the last picture, so this was never a record. Put the bytes back.
            section(record, 8)
            return
        end = min(position + length, len(data))
        if record_type == 0xF007:
            # A blip store entry: 36 fixed bytes and a name, then the picture's own header.
            position = section(position, 36)
            name_length = data[position - 3]
            if name_length:
                position = section(position, name_length)
            position = section(position, 8)
            first_word, record_type, _inner_length = struct.unpack_from("<HHI", data, position - 8)
        for _uid in range(2 if first_word >> 4 in _TWO_UID_INSTANCES else 1):
            position = section(position, 16)
        position = section(position, 34 if record_type in (0xF01A, 0xF01B, 0xF01C) else 1)
        section(position, end - position)
        position = end


def _overwrite_streams(path: Path, streams: dict[str, bytearray | None]) -> None:
    """Replace stream contents in place. Sizes never change, so the container stays valid."""
    import olefile

    ole = olefile.OleFileIO(str(path), write_mode=True)
    try:
        for name, data in streams.items():
            if data is None:
                continue
            if ole.get_size(name) >= ole.minisectorcutoff:
                ole.write_stream(name, bytes(data))
            else:
                _overwrite_mini_stream(ole, name, bytes(data))
    finally:
        ole.close()


def _overwrite_mini_stream(ole, name: str, data: bytes) -> None:
    """olefile cannot write streams under 4 KB, which live inside the shared mini stream."""
    entry = ole.direntries[ole._find(name)]
    ole.openstream(name).read()  # makes olefile load the mini FAT
    root_sectors = []
    sector = ole.root.isectStart
    while sector not in (0xFFFFFFFE, 0xFFFFFFFF):
        root_sectors.append(sector)
        sector = ole.fat[sector]
    mini_size, big_size = ole.minisectorsize, ole.sectorsize
    sector = entry.isectStart
    written = 0
    while sector not in (0xFFFFFFFE, 0xFFFFFFFF) and written < len(data):
        inside = sector * mini_size
        absolute = (root_sectors[inside // big_size] + 1) * big_size + inside % big_size
        ole.fp.seek(absolute)
        ole.fp.write(data[written : written + mini_size])
        written += mini_size
        sector = ole.minifat[sector]


def encrypt_pptx(path: str | Path, password: str, out_path: str | Path) -> None:
    """Write a password-protected copy of a PPTX, replacing `out_path` only once it is complete."""
    from msoffcrypto.format.ooxml import OOXMLFile

    out_path = Path(out_path)
    temporary = out_path.with_name(f".{out_path.stem}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with open(path, "rb") as handle, open(temporary, "wb") as output:
            OOXMLFile(handle).encrypt(password, output)
        os.replace(temporary, out_path)
    finally:
        temporary.unlink(missing_ok=True)
