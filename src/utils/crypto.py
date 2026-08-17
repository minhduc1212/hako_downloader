"""
Docln Chapter Decryption Engine (Base64 + XOR + Segment Shuffle Removal)
"""

import json
import base64
from typing import Union, List


def decrypt_hako_chapter(data_c_json: Union[str, list], data_k: str) -> str:
    """
    Decrypts Docln encrypted chapter payload (Base64 + XOR with multi-segment shuffle removal).

    Args:
        data_c_json: JSON string or list of encrypted segment strings
        data_k: Decryption key string from data-k attribute

    Returns:
        Decrypted HTML content string
    """
    if not data_c_json or not data_k:
        return ""

    if isinstance(data_c_json, str):
        try:
            chunks = json.loads(data_c_json)
        except Exception:
            return ""
    else:
        chunks = list(data_c_json)

    if not isinstance(chunks, list) or not chunks:
        return ""

    # Sort chunks by the 4-digit index prefix to undo client-side shuffle
    try:
        chunks.sort(key=lambda x: int(x[:4]))
    except Exception:
        pass

    decrypted_text = ""
    key_length = len(data_k)
    if key_length == 0:
        return ""

    for chunk in chunks:
        if len(chunk) <= 4:
            continue
        # Strip the 4-digit index prefix
        encoded_data = chunk[4:]
        try:
            decoded_bytes = base64.b64decode(encoded_data)
        except Exception:
            continue

        # XOR decryption with cyclic key
        decrypted_bytes = bytearray(
            byte ^ ord(data_k[i % key_length]) for i, byte in enumerate(decoded_bytes)
        )
        decrypted_text += decrypted_bytes.decode("utf-8", errors="replace")

    return decrypted_text
