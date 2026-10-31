"""Распознавание команд в тексте обращения к Милки (поиск арта, генерация картинки)."""
import re

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
