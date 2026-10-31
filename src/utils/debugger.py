# src/utils/debugger.py
import inspect
import os
import traceback

from src.utils.logger import log


class Debugger:
    def __init__(self, name):
        filename = os.path.basename(name)
        log.debug(f"ОТЛАДКА ФАЙЛА {filename.upper()}")

        # Сохраняем глобальное пространство вызывающего модуля
        self.caller_globals = self._get_caller_globals()
        # Создаем отдельное пространство для локальных переменных
        self.caller_locals = {}

    def _get_caller_globals(self):
        """
        Получает глобальное пространство модуля, который вызвал отладчик
        """
        frame = inspect.currentframe()
        # Пропускаем текущий фрейм (Debugger.__init__)
        # И фрейм вызова (место где создается Debugger)
        caller_frame = frame.f_back.f_back

        # Получаем глобальные переменные вызывающего модуля
        return caller_frame.f_globals

    def execute(self, cmd):
        """
        Выполняет команду в контексте вызывающего модуля и выводит результат
        """
        if not cmd or cmd.strip() == "":
            return

        try:
            # Выполняем команду и сохраняем результат
            # Используем compile чтобы получить AST и определить тип команды
            code = compile(cmd, "<debugger>", "eval")
            result = eval(code, self.caller_globals, self.caller_locals)
            if result is not None:
                print(f"< {repr(result)}")
            else:
                print("< None")

        except SyntaxError:
            # Если это не выражение, а оператор (например, присваивание)
            try:
                exec(cmd, self.caller_globals, self.caller_locals)
                print("< OK")
            except Exception as e:
                log.error(f"Ошибка exec: {e}")
                log.error(traceback.format_exc())
        except Exception as e:
            log.error(f"Ошибка eval: {e}")
            log.error(traceback.format_exc())

    def execute_cycle(self):
        """
        Интерактивный цикл с доступом к переменным вызывающего модуля
        """
        log.debug('ПЕРЕХОД В ИНТЕРАКТИВНЫЙ РЕЖИМ. ВВЕДИТЕ "exit" ДЛЯ ВЫХОДА')

        # Выводим доступные переменные
        public_vars = {
            k: v
            for k, v in self.caller_globals.items()
            if not k.startswith("_")
            and k not in ["Debugger", "log", "os", "sys", "inspect", "traceback"]
        }
        if public_vars:
            log.info(f"Доступные переменные: {', '.join(public_vars.keys())}")

        while True:
            try:
                cmd = input(">>> ")

                if cmd.lower() in ["exit", "quit", "q"]:
                    log.debug("ВЫХОД ИЗ ИНТЕРАКТИВНОГО РЕЖИМА")
                    break

                if cmd.strip():
                    self.execute(cmd)

            except KeyboardInterrupt:
                log.info("Выход")
                break
            except EOFError:
                break
            except Exception as e:
                log.error(f"Ошибка: {e}")
