"""Таймер «не чаще одного обращения за N секунд» на каждого человека."""
import time

from config import env, var


class Cooldown:
    def __init__(self):
        # user_id -> [когда таймер закончится, предупреждали ли уже человека]
        self._users = {}

    def check(self, user_id, now=None):
        """Проверяет человека и, если можно, запускает ему новый таймер.

        Возвращает (можно_ли, сколько_секунд_осталось, нужно_ли_предупредить).
        Предупреждать надо один раз за таймер: иначе бот сам станет спамить,
        отвечая на каждое лишнее сообщение.
        """
        seconds = var.user_cooldown_seconds
        if seconds <= 0 or user_id in env.owner_ids:  # владельцам бота таймер не мешает
            return True, 0.0, False

        now = time.monotonic() if now is None else now
        entry = self._users.get(user_id)
        if entry is None or now >= entry[0]:
            self._users[user_id] = [now + seconds, False]
            self._cleanup(now)
            return True, 0.0, False

        notify = not entry[1]
        entry[1] = True
        return False, entry[0] - now, notify

    def _cleanup(self, now):
        # Чтобы словарь не рос бесконечно, изредка выбрасываем истёкшие таймеры.
        if len(self._users) > 1000:
            self._users = {k: v for k, v in self._users.items() if v[0] > now}


cooldown = Cooldown()
