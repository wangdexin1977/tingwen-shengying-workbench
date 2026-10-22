"""Chinese spoken forms for OmniVoice; keep display text and other languages intact."""

from functools import lru_cache
import re
import unicodedata


# Keep unrelated model names such as F-35 and GPT-4o out of the general
# number normalizer: their hyphens are not negative signs.
_MODEL_NAME = re.compile(
    r"(?<![A-Za-z0-9_])[A-Za-z]+[ \t]*[-‐‑]?[ \t]*"
    r"[0-9]+[A-Za-z0-9]*(?:[-.][A-Za-z0-9]+)*(?![A-Za-z0-9_])"
)
_SM_NAME = re.compile(r"SM[ \t]*[-‐‑]?[ \t]*([0-9]+)", re.IGNORECASE)


@lru_cache(maxsize=1)
def _chinese_normalizer():
    from wetext import Normalizer

    return Normalizer(remove_erhua=False, lang="zh", operator="tn")


def normalize_omnivoice_text(text: str, language: str = "chinese") -> str:
    """Expand Chinese numbers and spell SM identifiers as letters plus Chinese numbers.

    Only the inference copy changes. Reference transcripts must retain their
    exact alignment with the reference audio.
    """
    selected = str(language or "None").lower()
    chinese = selected in {"chinese", "zh", "zh-cn", "中文"}
    auto_chinese = selected in {"none", "auto"} and re.search(r"[\u3400-\u9fff]", text)
    if not (chinese or auto_chinese) or not re.search(r"[0-9０-９]", text):
        return text
    value = unicodedata.normalize("NFKC", text)
    normalizer = _chinese_normalizer()
    parts = []
    start = 0
    for match in _MODEL_NAME.finditer(value):
        parts.append(normalizer.normalize(value[start:match.start()]))
        model_name = match.group()
        sm = _SM_NAME.fullmatch(model_name)
        # Chinese phonetic spelling keeps the letter names in the same voice
        # instead of switching languages inside the short identifier.
        parts.append("艾斯艾姆" + normalizer.normalize(sm.group(1)) if sm else model_name)
        start = match.end()
    parts.append(normalizer.normalize(value[start:]))
    return "".join(parts)
