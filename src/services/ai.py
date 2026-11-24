# src/services/ai.py

import asyncio
import time
from collections import namedtuple

import aiohttp

from config import env, var
from src.services.history import history
from src.services.lorebook import lorebook
from src.utils.json_parser import dicts
from src.utils.logger import log

glog = log.getChild("GROQ")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


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


# Итог проверки одного провайдера: status - "ok", "fail" или "no_key".
ProviderCheck = namedtuple("ProviderCheck", "provider model status seconds")


class Api:
    def __init__(self):
        # История вынесена в отдельный сервис; публичные методы ниже оставлены
        # для совместимости с командами и статистикой бота.
        self.history = history
        self._groq_session: aiohttp.ClientSession | None = None

    def trim_text(self, result: str) -> str:
        if not result:
            return result
        result = result.rstrip()
        if not result:
            return result
        if result[-1] in END_CHARS or _is_emoji(result[-1]):
            return result
        search_window = result[-70:]
        last_valid_index = -1
        for i in range(len(search_window) - 1, -1, -1):
            if search_window[i] in END_CHARS:
                last_valid_index = i
                break
        if last_valid_index != -1:
            offset = len(result) - len(search_window)
            return result[: offset + last_valid_index + 1]
        return result

    def _build_messages(self, user_id, user_name, message, lorebook_context=None):
        return self.history.build_messages(
            user_id, user_name, message, lorebook_context=lorebook_context
        )

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
            "thinking": {"type": "disabled"},
        }
        provider_log = log.getChild("CLOUDFLARE")
        provider_log.info(f"Запрос модели {model}")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    headers={"Authorization": f"Bearer {env.cloudflare_api_token}"},
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=var.cloudflare_timeout),
                ) as response:
                    if response.status == 429:
                        provider_log.warning(
                            "HTTP 429 — дневной лимит neurons исчерпан"
                        )
                        return None
                    if response.status != 200:
                        body = (await response.text())[:200]
                        provider_log.warning(
                            f"{model}: HTTP {response.status}: {body}"
                        )
                        return None

                    data = await response.json()
                    if not data.get("success"):
                        errors = data.get("errors") or []
                        detail = errors[0].get("message", "unknown") if errors else "unknown"
                        provider_log.warning(f"{model}: ошибка API: {detail}")
                        return None

                    return data.get("result", {}).get("response")
        except asyncio.TimeoutError:
            provider_log.warning(f"{model}: таймаут ожидания ответа")
        except aiohttp.ClientError:
            provider_log.exception(f"{model}: ошибка сетевого соединения")
        except Exception:
            provider_log.exception(f"{model}: непредвиденная ошибка")
        return None

    def _get_groq_session(self) -> aiohttp.ClientSession:
        if self._groq_session is None or self._groq_session.closed:
            self._groq_session = aiohttp.ClientSession()
        return self._groq_session

    async def close(self):
        if self._groq_session and not self._groq_session.closed:
            await self._groq_session.close()

    # GROQ
    async def _call_groq(self, model: str, messages: list[dict]) -> str | None:
        session = self._get_groq_session()
        headers = {"Authorization": f"Bearer {env.groq_api_key}"}
        payload = {
            "model": model,
            "messages": messages,
            "temperature": var.temperature,
            "max_tokens": var.max_tokens,
            "top_p": var.top_p,
        }
        glog.info(f"Запрос модели {model}")

        try:
            async with session.post(
                GROQ_URL,
                headers=headers,
                json=payload,  # Content-Type выставится сам
                timeout=aiohttp.ClientTimeout(total=var.groq_timeout),
            ) as response:
                if response.status == 429:
                    retry = response.headers.get("retry-after")
                    glog.warning(f"{model}: HTTP 429, retry-after={retry}")
                    return None
                if response.status != 200:
                    body = (await response.text())[:200]
                    glog.warning(f"{model}: HTTP {response.status}: {body}")
                    return None

                data = await response.json()
                return data["choices"][0]["message"]["content"]
        except asyncio.TimeoutError:
            glog.warning(f"{model}: таймаут ожидания ответа")
        except aiohttp.ClientError:
            glog.exception(f"{model}: ошибка сетевого соединения")
        except (KeyError, IndexError):
            glog.exception(f"{model}: некорректная структура ответа API")
        except Exception:
            glog.exception(f"{model}: непредвиденная ошибка")
        return None

    # OPENROUTER
    @staticmethod
    async def _call_openrouter(model, messages):
        if not env.openrouter_api_key:
            return None

        provider_log = log.getChild("OPENROUTER")
        provider_log.info(f"Запрос модели {model}")
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
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=var.openrouter_timeout),
                ) as response:
                    if response.status != 200:
                        body = (await response.text())[:200]
                        provider_log.warning(
                            f"{model}: HTTP {response.status}: {body}"
                        )
                        return None

                    data = await response.json()
                    return data["choices"][0]["message"]["content"]
        except asyncio.TimeoutError:
            provider_log.warning(f"{model}: таймаут ожидания ответа")
        except aiohttp.ClientError:
            provider_log.exception(f"{model}: ошибка сетевого соединения")
        except (KeyError, IndexError):
            provider_log.exception(f"{model}: некорректная структура ответа API")
        except Exception:
            provider_log.exception(f"{model}: непредвиденная ошибка")
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

        provider_log = log.getChild("ZAI")
        provider_log.info(f"Запрос модели {model}")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://api.z.ai/api/paas/v4/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=var.zai_timeout),
                ) as response:
                    if response.status == 429:
                        provider_log.warning(
                            f"{model}: HTTP 429 — ограничение частоты запросов"
                        )
                        return None
                    if response.status != 200:
                        body = (await response.text())[:200]
                        provider_log.warning(
                            f"{model}: HTTP {response.status}: {body}"
                        )
                        return None

                    data = await response.json()
                    choices = data.get("choices") or []
                    if not choices:
                        provider_log.warning(f"{model}: API вернул пустой список choices")
                        return None

                    content = (choices[0].get("message") or {}).get("content")
                    if not content:
                        provider_log.warning(f"{model}: API вернул пустой content")
                        return None

                    return content
        except asyncio.TimeoutError:
            provider_log.warning(f"{model}: таймаут ожидания ответа")
        except aiohttp.ClientError:
            provider_log.exception(f"{model}: ошибка сетевого соединения")
        except (KeyError, IndexError, TypeError):
            provider_log.exception(f"{model}: некорректная структура ответа API")
        except Exception:
            provider_log.exception(f"{model}: непредвиденная ошибка")
        return None

    # ВЫЗОВ ПРОВАЙДЕРА ПО ИМЕНИ
    async def call_provider(self, provider, model, messages):
        """Вызывает нужного провайдера. Для неизвестного пишет в лог и возвращает None."""
        callers = {
            "cloudflare": self._call_cloudflare,
            "zai": self._call_zai,
            "openrouter": self._call_openrouter,
            "groq": self._call_groq,
        }
        caller = callers.get(provider)
        if caller is None:
            log.warning(f"Неизвестный провайдер: {provider}")
            return None
        return await caller(model, messages)

    # ПРОВЕРКА ПРОВАЙДЕРОВ (для админ-команды root# ai)
    @staticmethod
    def has_credentials(provider):
        """Заданы ли в .env ключи для провайдера."""
        if provider == "cloudflare":
            return bool(env.cloudflare_account_id and env.cloudflare_api_token)
        keys = {
            "zai": env.zai_api_key,
            "openrouter": env.openrouter_api_key,
            "groq": env.groq_api_key,
        }
        return bool(keys.get(provider))

    async def check_providers(self):
        """Один короткий запрос к каждому провайдеру из цепочки, все сразу.

        Ничего не пишет в историю. Возвращает список ProviderCheck в порядке цепочки.
        """
        messages = [{"role": "user", "content": "Ответь одним словом: ок"}]

        async def probe(provider, model):
            if not self.has_credentials(provider):
                return ProviderCheck(provider, model, "no_key", 0.0)
            started = time.monotonic()
            try:
                result = await self.call_provider(provider, model, messages)
            except Exception:
                log.getChild(provider.upper()).exception(
                    f"Проверка модели {model} завершилась исключением"
                )
                result = None
            ok = isinstance(result, str) and bool(result.strip())
            return ProviderCheck(
                provider, model, "ok" if ok else "fail", time.monotonic() - started
            )

        return list(
            await asyncio.gather(*(probe(p, m) for p, m in var.fallback_models))
        )

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
                result = await self.call_provider(provider, model, messages)

                if result and isinstance(result, str) and result.strip():
                    result = result.strip()
                    # Сохраняем привычное для Discord экранирование, но не историю.
                    return result.replace("*", "\\*")
            except Exception:
                log.getChild(provider_name).exception(
                    f"Ошибка отдельного запроса модели {model}"
                )

        return None

    # ЗАПРОС ИИ
    async def ask_ai(self, user_id, user_name, message):
        lorebook_context = None
        if lorebook is not None and var.lorebook_enabled:
            try:
                depth = max(0, var.lorebook_scan_depth)
                recent = self.history.get_recent_messages(user_id, depth + 1)
                previous_assistant = None

                # Последняя assistant-запись — предыдущий ответ Милки. Её используем
                # как источник триггеров, но не включаем в окно проверки повторов.
                if recent and recent[-1]["role"] == "assistant":
                    previous_assistant = recent[-1]["content"]
                    earlier_messages = recent[:-1][-depth:] if depth else []
                else:
                    # После неудачного запроса последняя запись может быть user.
                    earlier_messages = recent[-depth:] if depth else []

                lorebook_context = lorebook.build_context(
                    current_message=message,
                    previous_assistant=previous_assistant,
                    earlier_messages=earlier_messages,
                )
            except Exception:
                log.getChild("LOREBOOK").exception(
                    "Не удалось подготовить контекст Lorebook"
                )

        messages = self._build_messages(
            user_id, user_name, message, lorebook_context=lorebook_context
        )

        for provider, model in var.fallback_models:
            provider_name = provider.upper()
            try:
                result = await self.call_provider(provider, model, messages)

                if result:
                    if result in dicts.get("censor_phrases"):
                        result = dicts.get_random("censor_replies")
                        log.getChild("FILTER").info(
                            "Цензурная заглушка заменена на реплику Экспи"
                        )
                    result = self.trim_text(result)

                    return self._save_response(user_id, result)

            except Exception:
                log.getChild(provider_name).exception(
                    f"Ошибка при обработке ответа модели {model}"
                )
                continue

        # В СЛУЧАЕ ОТКАЗА ВСЕХ
        return dicts.get("api_failure")


api = Api()
