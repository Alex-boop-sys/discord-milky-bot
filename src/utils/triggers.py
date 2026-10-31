"""Распознавание команд в тексте обращения к Милки (поиск арта, генерация картинки)."""
import re

from config import var

# \b - граница слова: «арт» сработает на «арт лиса», но не на «артём».
ART_RE = re.compile(r"^(?:найди\s+арт|арт|art|рисунок)\b[\s,:]*")
GEN_RE = re.compile(
    r"^(?:сгенерируй|генерируй|генерация|ген|gen"
    r"|(?:создай|сделай)\s+(?:картинку|изображение|рисунок))\b[\s,:]*"
)


def match_trigger(request):
    """Возвращает ("art" | "gen", остаток запроса) либо None, если это обычное сообщение."""
    text = request.lower().strip()
    for kind, pattern in (("art", ART_RE), ("gen", GEN_RE)):
        match = pattern.match(text)
        if match:
            return kind, text[match.end():].strip()
    return None


ESCAPE = "\\"


def looks_like_foreign_command(text):
    """Похож ли запрос на команду другого бота или на просьбу «не отвечай».

    Да, если он начинается со знака из var.ignored_prefixes и сразу за знаком идёт
    буква или цифра («!play», «.r d20», «/help»), а также всегда, если он начинается
    с обратной косой черты. Знаки с пробелом или пунктуацией после них («: привет»,
    «- привет», «...», «:)») командой не считаются.
    """
    text = text.lstrip()
    if not text or text[0] not in var.ignored_prefixes:
        return False
    return text[0] == ESCAPE or text[1:2].isalnum()
