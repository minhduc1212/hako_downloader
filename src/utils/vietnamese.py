"""
Vietnamese Unicode Normalization and Character De-obfuscation Engine
Fixes broken, decomposed, and split Vietnamese characters caused by OCR or web-font obfuscation:
e.g. 'đê ´ n' -> 'đến', 'dâ n' -> 'dân', 'khuâ ´ t' -> 'khuất', 'hô n' -> 'hôn', 'ngươ ` i' -> 'người'
"""

import re
import unicodedata
from typing import Dict, Tuple

# Diphthong tone mappings (ươ, uô, iê, yê, etc.)
DIPHTHONG_TONE_MAP: Dict[Tuple[str, str], str] = {
    # ươ
    ('ươ', 'sac'): 'ướ', ('ươ', 'huyen'): 'ườ', ('ươ', 'hoi'): 'ưở', ('ươ', 'nga'): 'ưỡng', ('ươ', 'nang'): 'ượ',
    ('Ươ', 'sac'): 'Ướ', ('Ươ', 'huyen'): 'Ườ', ('Ươ', 'hoi'): 'Ưở', ('Ươ', 'nga'): 'Ưỡng', ('Ươ', 'nang'): 'Ượ',
    ('ƯƠ', 'sac'): 'ƯỚ', ('ƯƠ', 'huyen'): 'ƯỜ', ('ƯƠ', 'hoi'): 'ƯỞ', ('ƯƠ', 'nga'): 'ƯỠNG', ('ƯƠ', 'nang'): 'ƯỢ',
    
    # uô
    ('uô', 'sac'): 'uố', ('uô', 'huyen'): 'uồ', ('uô', 'hoi'): 'uổ', ('uô', 'nga'): 'uỗ', ('uô', 'nang'): 'uộ',
    ('Uô', 'sac'): 'Uố', ('Uô', 'huyen'): 'Uồ', ('Uô', 'hoi'): 'Uổ', ('Uô', 'nga'): 'Uỗ', ('Uô', 'nang'): 'Uộ',
    ('UÔ', 'sac'): 'UỐ', ('UÔ', 'huyen'): 'UỒ', ('UÔ', 'hoi'): 'UỔ', ('UÔ', 'nga'): 'UỖ', ('UÔ', 'nang'): 'UỘ',

    # iê
    ('iê', 'sac'): 'iế', ('iê', 'huyen'): 'iề', ('iê', 'hoi'): 'iể', ('iê', 'nga'): 'iễ', ('iê', 'nang'): 'iệ',
    ('Iê', 'sac'): 'Iế', ('Iê', 'huyen'): 'Iề', ('Iê', 'hoi'): 'Iể', ('Iê', 'nga'): 'Iễ', ('Iê', 'nang'): 'Iệ',
    ('IÊ', 'sac'): 'IẾ', ('IÊ', 'huyen'): 'IỀ', ('IÊ', 'hoi'): 'IỂ', ('IÊ', 'nga'): 'IỄ', ('IÊ', 'nang'): 'IỆ',

    # yê
    ('yê', 'sac'): 'yế', ('yê', 'huyen'): 'yề', ('yê', 'hoi'): 'yể', ('yê', 'nga'): 'yễ', ('yê', 'nang'): 'yệ',
    ('Yê', 'sac'): 'Yế', ('Yê', 'huyen'): 'Yề', ('Yê', 'hoi'): 'Yể', ('Yê', 'nga'): 'Yễ', ('Yê', 'nang'): 'Yệ',
    ('YÊ', 'sac'): 'YẾ', ('YÊ', 'huyen'): 'YỀ', ('YÊ', 'hoi'): 'YỂ', ('YÊ', 'nga'): 'YỄ', ('YÊ', 'nang'): 'YỆ',
}

# Single vowel tone mappings
VOWEL_ACCENT_MAP: Dict[Tuple[str, str], str] = {
    # a
    ('a', 'sac'): 'á', ('a', 'huyen'): 'à', ('a', 'hoi'): 'ả', ('a', 'nga'): 'ã', ('a', 'nang'): 'ạ',
    ('A', 'sac'): 'Á', ('A', 'huyen'): 'À', ('A', 'hoi'): 'Ả', ('A', 'nga'): 'Ã', ('A', 'nang'): 'Ạ',
    # ă
    ('ă', 'sac'): 'ắ', ('ă', 'huyen'): 'ằ', ('ă', 'hoi'): 'ẳ', ('ă', 'nga'): 'ẵ', ('ă', 'nang'): 'ặ',
    ('Ă', 'sac'): 'Ắ', ('Ă', 'huyen'): 'Ằ', ('Ă', 'hoi'): 'Ẳ', ('Ă', 'nga'): 'Ẵ', ('Ă', 'nang'): 'Ặ',
    # â
    ('â', 'sac'): 'ấ', ('â', 'huyen'): 'ầ', ('â', 'hoi'): 'ẩ', ('â', 'nga'): 'ẫ', ('â', 'nang'): 'ậ',
    ('Â', 'sac'): 'Ấ', ('Â', 'huyen'): 'Ầ', ('Â', 'hoi'): 'Ẩ', ('Â', 'nga'): 'Ẫ', ('Â', 'nang'): 'Ậ',
    # e
    ('e', 'sac'): 'é', ('e', 'huyen'): 'è', ('e', 'hoi'): 'ẻ', ('e', 'nga'): 'ẽ', ('e', 'nang'): 'ẹ',
    ('E', 'sac'): 'É', ('E', 'huyen'): 'È', ('E', 'hoi'): 'Ẻ', ('E', 'nga'): 'Ẽ', ('E', 'nang'): 'Ẹ',
    # ê
    ('ê', 'sac'): 'ế', ('ê', 'huyen'): 'ề', ('ê', 'hoi'): 'ể', ('ê', 'nga'): 'ễ', ('ê', 'nang'): 'ệ',
    ('Ê', 'sac'): 'Ế', ('Ê', 'huyen'): 'Ề', ('Ê', 'hoi'): 'Ể', ('Ê', 'nga'): 'Ễ', ('Ê', 'nang'): 'Ệ',
    # i
    ('i', 'sac'): 'í', ('i', 'huyen'): 'ì', ('i', 'hoi'): 'ỉ', ('i', 'nga'): 'ĩ', ('i', 'nang'): 'ị',
    ('I', 'sac'): 'Í', ('I', 'huyen'): 'Ì', ('I', 'hoi'): 'Ỉ', ('I', 'nga'): 'Ĩ', ('I', 'nang'): 'Ị',
    # o
    ('o', 'sac'): 'ó', ('o', 'huyen'): 'ò', ('o', 'hoi'): 'ỏ', ('o', 'nga'): 'õ', ('o', 'nang'): 'ọ',
    ('O', 'sac'): 'Ó', ('O', 'huyen'): 'Ò', ('O', 'hoi'): 'Ỏ', ('O', 'nga'): 'Õ', ('O', 'nang'): 'Ọ',
    # ô
    ('ô', 'sac'): 'ố', ('ô', 'huyen'): 'ồ', ('ô', 'hoi'): 'ổ', ('ô', 'nga'): 'ỗ', ('ô', 'nang'): 'ộ',
    ('Ô', 'sac'): 'Ố', ('Ô', 'huyen'): 'Ồ', ('Ô', 'hoi'): 'Ổ', ('Ô', 'nga'): 'Ỗ', ('Ô', 'nang'): 'Ộ',
    # ơ
    ('ơ', 'sac'): 'ớ', ('ơ', 'huyen'): 'ờ', ('ơ', 'hoi'): 'ở', ('ơ', 'nga'): 'ỡ', ('ơ', 'nang'): 'ợ',
    ('Ơ', 'sac'): 'Ớ', ('Ơ', 'huyen'): 'Ờ', ('Ơ', 'hoi'): 'Ở', ('Ơ', 'nga'): 'Ỡ', ('Ơ', 'nang'): 'Ợ',
    # u
    ('u', 'sac'): 'ú', ('u', 'huyen'): 'ù', ('u', 'hoi'): 'ủ', ('u', 'nga'): 'ũ', ('u', 'nang'): 'ụ',
    ('U', 'sac'): 'Ú', ('U', 'huyen'): 'Ù', ('U', 'hoi'): 'Ủ', ('U', 'nga'): 'Ũ', ('U', 'nang'): 'Ụ',
    # ư
    ('ư', 'sac'): 'ứ', ('ư', 'huyen'): 'ừ', ('ư', 'hoi'): 'ử', ('ư', 'nga'): 'ữ', ('ư', 'nang'): 'ự',
    ('Ư', 'sac'): 'Ứ', ('Ư', 'huyen'): 'Ừ', ('Ư', 'hoi'): 'Ử', ('Ư', 'nga'): 'Ữ', ('Ư', 'nang'): 'Ự',
    # y
    ('y', 'sac'): 'ý', ('y', 'huyen'): 'ỳ', ('y', 'hoi'): 'ỷ', ('y', 'nga'): 'ỹ', ('y', 'nang'): 'ỵ',
    ('Y', 'sac'): 'Ý', ('Y', 'huyen'): 'Ỳ', ('Y', 'hoi'): 'Ỷ', ('Y', 'nga'): 'Ỹ', ('Y', 'nang'): 'Ỵ',
}

TONE_LOOKUP: Dict[str, str] = {
    '´': 'sac', 'ˊ': 'sac', '\u00B4': 'sac', '\u02CA': 'sac', '\u0301': 'sac',
    '`': 'huyen', 'ˋ': 'huyen', '\u0060': 'huyen', '\u02CB': 'huyen', '\u0300': 'huyen',
    '̉': 'hoi', '\u0309': 'hoi',
    '~': 'nga', '˜': 'nga', '\u02DC': 'nga', '̃': 'nga', '\u0303': 'nga',
    '̣': 'nang', '\u0323': 'nang', '˙': 'nang', '\u02D9': 'nang'
}

TONE_CHARS_PATTERN = ''.join(re.escape(k) for k in TONE_LOOKUP.keys())
PATTERN_DIPHTHONG = re.compile(r'(ươ|Ươ|ƯƠ|uô|Uô|UÔ|iê|Iê|IÊ|yê|Yê|YÊ)\s*([' + TONE_CHARS_PATTERN + r'])')
PATTERN_SINGLE_TONE = re.compile(r'([aăâeêioôơuưyAĂÂEÊIOÔƠUƯY])\s*([' + TONE_CHARS_PATTERN + r'])')

TRAILING_ELEMENTS = r'(?:ng|nh|ch|[cmnptk]|u|i|o|y)'
SYLLABLE_BASE = r'(\b[a-zA-Z\u00C0-\u1EF9]*[âêôơưăáàảãạắằẳẵặấầẩẫậéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵÁÀẢÃẠẮẰẲẴẶẤẦẨẪẬÉÈẺẼẸẾỀỂỄỆÍÌỈĨỊÓÒỎÕỌỐỒỔỖỘỚỜỞỠỢÚÙỦŨỤỨỪỬỮỰÝỲỶỸỴ])'
PATTERN_RECONNECT = re.compile(SYLLABLE_BASE + r'\s+(' + TRAILING_ELEMENTS + r')(?=[^\w\u00C0-\u1EF9]|$)', re.IGNORECASE)


def clean_vietnamese_text(text: str) -> str:
    """
    Repairs split Vietnamese letters, removes non-breaking and invisible spaces,
    and normalizes into pure Unicode NFC.
    """
    if not text:
        return ""

    # 1. Strip non-breaking spaces and invisible characters
    text = (
        text.replace('\xa0', ' ')
        .replace('\u200b', '')
        .replace('\u200c', '')
        .replace('\u200d', '')
        .replace('\ufeff', '')
        .replace('\u00ad', '')
        .replace('\r', '')
    )

    # 2. Normalize combining characters that have spaces before them: 'e \u0301' -> 'e\u0301'
    text = re.sub(r'([a-zA-Z\u00C0-\u1EF9])\s+([\u0300-\u036F\u1DC0-\u1DFF\u1AB0-\u1AFF])', r'\1\2', text)

    # 3. Replace tone marks on diphthongs first (e.g. 'ngươ ` i' -> 'ngườ i')
    def repl_diph(m):
        diph = m.group(1)
        sym = m.group(2)
        tone_type = TONE_LOOKUP.get(sym)
        if tone_type:
            return DIPHTHONG_TONE_MAP.get((diph, tone_type), diph + sym)
        return m.group(0)

    text = PATTERN_DIPHTHONG.sub(repl_diph, text)

    # 4. Replace standalone tone marks on single vowels (e.g. 'đê ´ n' -> 'đế n', 'mâ ´ p' -> 'mấ p')
    def repl_tone(m):
        vowel = m.group(1)
        sym = m.group(2)
        tone_type = TONE_LOOKUP.get(sym)
        if tone_type:
            return VOWEL_ACCENT_MAP.get((vowel, tone_type), vowel + sym)
        return m.group(0)

    text = PATTERN_SINGLE_TONE.sub(repl_tone, text)

    # 5. Reconnect split Vietnamese syllables (e.g. 'đế n' -> 'đến', 'dâ n' -> 'dân', 'khuấ t' -> 'khuất', 'hôn' -> 'hôn')
    for _ in range(2):
        text = PATTERN_RECONNECT.sub(r'\1\2', text)

    # 6. Collapse redundant whitespace
    text = re.sub(r'[ \t]+', ' ', text)

    # 7. Canonical Unicode NFC Normalization
    text = unicodedata.normalize('NFC', text)
    return text.strip()
