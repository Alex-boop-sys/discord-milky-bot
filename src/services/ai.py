# src/services/ai.py

import aiohttp

from config import env, var
from src.services.history import history
from src.utils.json_parser import dicts
from src.utils.logger import log

# Знаки, которыми обычно заканчивается законченная фраза.
# Символы " и ' могут быть и открывающими, но чаще закрывают реплику.
END_CHARS = set(")]}!.?;:*~…”»\"'—")


# Эмодзи в конце сообщения тоже считаем нормальным окончанием.
def _is_emoji(ch: str) -> bool:
    code = ord(ch)
    return (
        0x1F000 <= code <= 0x1FAFF  # основные блоки эмодзи
        or 0x2600 <= code <= 0x27BF  # символы и dingbats (☀ ✨ ❤ и т.п.)
        or code in (0xFE0F, 0x200D)  # вариационный селектор и склейка эмодзи
    )


class Api:
    def __init__(self):
        # История вынесена в отдельный сервис; публичные методы ниже оставлены
        # для совместимости с командами и статистикой бота.
        self.history = history

    def trim_text(self, result: str) -> str:
        if not result:
            return result
        result = result.rstrip()
        if not result:
            return result
        if result[-1] in END_CHARS or _is_emoji(result[-1]):
            return result
        search_window = result[-100:]
        last_valid_index = -1
        for i in range(len(search_window) - 1, -1, -1):
            if search_window[i] in END_CHARS:
                last_valid_index = i
                break
        if last_valid_index != -1:
            offset = len(result) - len(search_window)
            return result[: offset + last_valid_index + 1]
        return result

    def _build_messages(self, user_id, user_name, message):
        return self.history.build_messages(user_id, user_name, message)

    def _save_response(self, user_id, text):
        return self.history.save_response(user_id, text)

    def clear_history(self, user_id):
        return self.history.clear_history(user_id)

    def get_top_users(self, limit=10):
        return self.history.get_top_users(limit)

    # API КЛИЕНТЫ
    @staticmethod
    async def _call_cloudflare(model, messages):
        if not env.cloudflare_account_id or not env.cloudflare_api_token:
            log.getChild("CLOUDFLARE").warning(
                "Ключи Cloudflare не настроены, пропускаем"
            )
            return None

        url = (
            f"https://api.cloudflare.com/client/v4/accounts/"
            f"{env.cloudflare_account_id}/ai/run/{model}"
        )

        payload = {
            "messages": messages,
            "max_tokens": var.max_tokens,
            "temperature": var.temperature,
            "top_p": var.top_p,
        }
        payload["thinking"] = {"type": "disabled"}

        async with aiohttp.ClientSession() as session:
            log.getChild("CLOUDFLARE").info(f"Попытка вызвать Cloudflare: {model}")
            try:
                async with session.post(
                    url,
                    headers={"Authorization": f"Bearer {env.cloudflare_api_token}"},
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=var.cloudflare_timeout),
                ) as response:
                    if response.status == 429:
                        log.getChild("CLOUDFLARE").warning(
                            "429 — дневной лимит neurons исчерпан"
                        )
                        return None
                    if response.status != 200:
                        log.getChild("CLOUDFLARE").warning(
                            f"Ошибка HTTP {response.status}"
                        )
                        return None

                    data = await response.json()

                    # Cloudflare оборачивает ответ в {success, result, errors}
                    if not data.get("success"):
                        err = data.get("errors", [{}])[0]
                        log.getChild("CLOUDFLARE").warning(
                            f"Ошибка API: {err.get('message', 'unknown')}"
                        )
                        return None

                    # Текст ответа лежит в result.response (для instruct-моделей)
                    result = data.get("result", {})
                    return result.get("response")

            except Exception as e:
                log.getChild("CLOUDFLARE").warning(f"Исключение: {e}")
                return None

    # GROQ
    @staticmethod
    async def _call_groq(model, messages):
        async with aiohttp.ClientSession() as session:
            log.getChild("GROQ").info(f"Попытка вызвать GROQ: {model}")
            headers = {
                "Authorization": f"Bearer {env.groq_api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": model,
                "messages": messages,
                "temperature": var.temperature,
                "max_tokens": var.max_tokens,
                "top_p": var.top_p,
            }
            try:
                async with session.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=var.groq_timeout),
                ) as response:
                    if response.status == 429:
                        log.warning(f"{model}: 429 — лимит")
                        return None
                    if response.status != 200:
                        log.warning(f"{model}: ошибка {response.status}")
                        return None
                    data = await response.json()
                    return data["choices"][0]["message"]["content"]
            except Exception as e:
                log.getChild("GROQ").warning(f"Исключение: {e}")
                return None

    # OPENROUTER
    @staticmethod
    async def _call_openrouter(model, messages):
        if not env.openrouter_api_key:
            return None
        async with aiohttp.ClientSession() as session:
            log.getChild("OPENROUTER").info(f"Попытка вызвать OpenRouter: {model}")
            headers = {
                "Authorization": f"Bearer {env.openrouter_api_key}",
                "HTTP-Referer": "https://github.com",
                "X-Title": "Expie Bot",
                "Content-Type": "application/json",
            }
            payload = {
                "model": model,
                "messages": messages,
                "temperature": var.temperature,
                "max_tokens": var.max_tokens,
                "top_p": var.top_p,
            }
            try:
                async with session.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=var.openrouter_timeout),
                ) as response:
                    if response.status != 200:
                        log.getChild("OPENROUTER").warning(
                            f"{model}: ошибка {response.status}"
                        )
                        return None
                    data = await response.json()
                    return data["choices"][0]["message"]["content"]
            except Exception as e:
                log.getChild("OPENROUTER").warning(f"Исключение: {e}")
                return None

    # Z.AI
    @staticmethod
    async def _call_zai(model, messages):
        if not env.zai_api_key:
            log.getChild("ZAI").warning("Ключ Z.ai не настроен, пропускаем")
            return None

        headers = {
            "Authorization": f"Bearer {env.zai_api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": model,
            "messages": messages,
            "temperature": var.temperature,
            "max_tokens": var.max_tokens,
            "top_p": var.top_p,
        }

        if model.startswith(("glm-4.5", "glm-4.7", "glm-4.8", "glm-5.")):
            payload["thinking"] = {"type": "disabled"}

        async with aiohttp.ClientSession() as session:
            log.getChild("ZAI").info(f"Попытка вызвать Z.ai: {model}")
            try:
                async with session.post(
                    "https://api.z.ai/api/paas/v4/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=var.zai_timeout),
                ) as response:
                    if response.status == 429:
                        log.getChild("ZAI").warning(f"{model}: 429 — rate limit")
                        return None
                    if response.status != 200:
                        body = await response.text()
                        log.getChild("ZAI").warning(
                            f"{model}: ошибка HTTP {response.status}: {body[:200]}"
                        )
                        return None

                    data = await response.json()

                    choices = data.get("choices") or []
                    if not choices:
                        log.getChild("ZAI").warning(
                            f"{model}: пустой ответ (нет choices)"
                        )
                        return None

                    message = choices[0].get("message", {})
                    content = message.get("content")

                    if not content:
                        log.getChild("ZAI").warning(
                            f"{model}: пустой content, пропускаем"
                        )
                        return None

                    return content

            except Exception as e:
                log.getChild("ZAI").warning(f"Исключение: {e}")
                return None

    # ОТДЕЛЬНЫЙ ЗАПРОС ИИ БЕЗ ИСТОРИИ ДИАЛОГА
    async def ask_standalone(self, message, system_prompt=None):
        """Запрашивает отдельный ответ, не записывая его в историю пользователя."""
        prompt = system_prompt or dicts.get("def_prompt")
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": message},
        ]

        for provider, model in var.fallback_models:
            provider_name = provider.upper()
            try:
                if provider == "cloudflare":
                    result = await self._call_cloudflare(model, messages)
                elif provider == "zai":
                    result = await self._call_zai(model, messages)
                elif provider == "openrouter":
                    result = await self._call_openrouter(model, messages)
                elif provider == "groq":
                    result = await self._call_groq(model, messages)
                else:
                    log.warning(f"Неизвестный провайдер: {provider}")
                    continue

                if result and isinstance(result, str) and result.strip():
                    result = result.strip()
                    # Сохраняем привычное для Discord экранирование, но не историю.
                    return result.replace("*", "\\*")
            except Exception as e:
                log.getChild(provider_name).warning(
                    f"Ошибка отдельного запроса модели {model}: {e}"
                )

        return None

    # ЗАПРОС ИИ
    async def ask_ai(self, user_id, user_name, message):
        messages = self._build_messages(user_id, user_name, message)

        for provider, model in var.fallback_models:
            provider_name = provider.upper()
            try:
                log.getChild(provider_name).info(f"Использование модели {model}")

                if provider == "cloudflare":
                    result = await self._call_cloudflare(model, messages)
                elif provider == "zai":
                    result = await self._call_zai(model, messages)
                elif provider == "openrouter":
                    result = await self._call_openrouter(model, messages)
                elif provider == "groq":
                    result = await self._call_groq(model, messages)
                else:
                    log.warning(f"Неизвестный провайдер: {provider}")
                    continue

                if result:
                    if result in dicts.get("censor_phrases"):
                        result = dicts.get_random("censor_replies")
                        log.getChild("FILTER").info(
                            "Цензурная заглушка заменена на реплику Экспи"
                        )
                    result = self.trim_text(result)

                    return self._save_response(user_id, result)

            except Exception as e:
                log.getChild(provider_name).warning(f"Ошибка модели {model}: {e}")
                continue

        # В СЛУЧАЕ ОТКАЗА ВСЕХ
        return dicts.get("api_failure")


api = Api()
