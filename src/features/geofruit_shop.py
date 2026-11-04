"""Персональный интерактивный магазин Геофруктов."""

import asyncio

import discord
from discord import app_commands

from src.features.economy import economy
from src.services.ai import api
from src.utils.json_parser import dicts
from src.utils.logger import log


# Каталог отделён от интерфейса. Позже новые лоты можно будет добавить сюда.
SHOP_ITEMS = (
    {
        "id": "treat_milky",
        "emoji": "🍓",
        "name": "Угостить Милки Геофруктом",
        "description": "Милки с аппетитом съест ягодку и искренне поблагодарит тебя.",
        "price": 1,
        "enabled": True,
    },
    {
        "id": "placeholder_2",
        "emoji": "🎁",
        "name": "Сюрприз №2",
        "description": "Этот товар пока готовится. Загляни сюда позже!",
        "price": 0,
        "enabled": False,
    },
    {
        "id": "placeholder_3",
        "emoji": "✨",
        "name": "Сюрприз №3",
        "description": "Этот товар пока готовится. Загляни сюда позже!",
        "price": 0,
        "enabled": False,
    },
)

# Защита от флуда общая для всех открытых магазинов, чтобы новый /магазин
# не позволял обойти таймер. asyncio.Lock не даёт двум покупкам идти параллельно.
SHOP_CLICK_COOLDOWN = 5.0
SHOP_AI_TIMEOUT = 55
_shop_last_click = {}
_shop_user_locks = {}


class GeoFruitShopView(discord.ui.View):
    """Персональная панель магазина. Само сообщение видно всему каналу."""

    def __init__(self, owner_id: int):
        super().__init__(timeout=15 * 60)
        self.owner_id = owner_id
        self._closed = False

        for item in SHOP_ITEMS:
            button = discord.ui.Button(
                label="Купить" if item["enabled"] else "Скоро",
                emoji=item["emoji"],
                style=discord.ButtonStyle.success if item["enabled"] else discord.ButtonStyle.secondary,
                custom_id=f"geofruit_shop:{item['id']}:{owner_id}",
                disabled=not item["enabled"],
            )
            button.callback = self._make_callback(item)
            self.add_item(button)

    def _make_callback(self, item):
        async def callback(interaction: discord.Interaction):
            await self._handle_purchase(interaction, item)

        return callback

    async def _handle_purchase(self, interaction: discord.Interaction, item):
        # Проверяем владельца до любых изменений баланса.
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                f"{interaction.user.mention} это не твои кнопочки! "
                "Становись в очередь и используй свой /магазин",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        if self._closed:
            await interaction.response.send_message(
                "Магазин уже закрылся. Открой новый через /магазин.",
                ephemeral=True,
            )
            return

        now = asyncio.get_running_loop().time()
        last_click = _shop_last_click.get(interaction.user.id, 0.0)
        remaining = SHOP_CLICK_COOLDOWN - (now - last_click)
        if remaining > 0:
            await interaction.response.send_message(
                f"Не так быстро! Подожди ещё {remaining:.1f} сек. 🫐",
                ephemeral=True,
            )
            return

        lock = _shop_user_locks.setdefault(interaction.user.id, asyncio.Lock())
        if lock.locked():
            await interaction.response.send_message(
                "Милки уже обрабатывает твою покупку, секундочку!",
                ephemeral=True,
            )
            return

        async with lock:
            # Ставим cooldown только на принятые покупки, а не на неудачный запрос.
            _shop_last_click[interaction.user.id] = now
            await interaction.response.defer(thinking=True, ephemeral=True)

            if item["id"] == "treat_milky":
                await self._buy_treat(interaction, item)
                return

            await interaction.followup.send("Этот товар пока не реализован.", ephemeral=True)

    async def _buy_treat(self, interaction: discord.Interaction, item):
        # Сначала атомарно списываем валюту. Если AI/API не ответит, покупка
        # всё равно считается совершённой и будет выдан шаблонный ответ.
        new_balance = economy.spend_balance(interaction.user.id, item["price"])
        if new_balance is None:
            current_balance = economy.get_balance(interaction.user.id)
            await interaction.followup.send(
                f"У тебя недостаточно Геофруктов! Нужно **{item['price']} 🫐**, "
                f"а сейчас **{current_balance} 🫐**.",
                ephemeral=True,
            )
            return

        user_name = interaction.user.display_name
        request = (
            f"Пользователь {user_name} (Discord: <@{interaction.user.id}>) "
            "угостил тебя вкуснейшим Геофруктом. Подробно и живо опиши, как ты "
            "берёшь его своими лапками, с аппетитом откусываешь и ешь, а затем "
            "искренне благодаришь пользователя. Пиши от лица Милки, в её характере. "
            "Ответ — готовая реплика для Discord, без пояснений."
        )

        fallback = (
            f"*Милки бережно принимает Геофрукт лапками, с удовольствием хрумкает "
            f"им и довольно улыбается.* Мр-р, спасибо тебе, {user_name}! 🫐💕"
        )

        try:
            response = await asyncio.wait_for(
                api.ask_standalone(
                    request,
                    system_prompt=dicts.get("def_prompt"),
                ),
                timeout=SHOP_AI_TIMEOUT,
            )
        except asyncio.TimeoutError:
            log.warning(f"Магазин: AI timeout для пользователя {interaction.user.id}")
            response = None
        except Exception as exc:
            log.warning(f"Магазин: AI ошибка для пользователя {interaction.user.id}: {exc}")
            response = None

        if not response or not response.strip():
            response = fallback

        # Обновляем баланс на публичной панели магазина, если сообщение ещё доступно.
        if self.message and self.message.embeds:
            embed = self.message.embeds[0].copy()
            if embed.fields:
                embed.set_field_at(
                    0,
                    name="👛 Твой баланс",
                    value=f"**{new_balance}** {economy.currency_word(new_balance)} 🫐",
                    inline=False,
                )
            try:
                await self.message.edit(embed=embed)
            except discord.HTTPException:
                pass

        await interaction.followup.send(
            f"{response.strip()}\n\n"
            f"*Потрачено: {item['price']} 🫐 • Остаток: {new_balance} 🫐*",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        log.info(
            f"Магазин: пользователь {interaction.user.id} купил {item['id']} "
            f"за {item['price']} Геофрукт(ов); остаток {new_balance}"
        )

    async def on_timeout(self):
        self._closed = True
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        # При timeout Discord не гарантирует, что исходное сообщение ещё доступно,
        # поэтому обновление сообщения выполняется best-effort.
        try:
            if self.message:
                await self.message.edit(view=self)
        except discord.HTTPException:
            pass


class GeoFruitShop:
    """Регистрация команды магазина."""

    @staticmethod
    def register():
        from src.bot.bot import bot

        @bot.tree.command(name="магазин", description="Открыть лавку Геофруктов 🫐")
        @app_commands.guild_only()
        async def shop(interaction: discord.Interaction):
            if not economy.enabled_in(interaction.guild_id):
                await interaction.response.send_message(
                    dicts.get("not_available_here"), ephemeral=True
                )
                return

            balance = economy.get_balance(interaction.user.id)
            embed = discord.Embed(
                title="🫐 Лавка Милки",
                description=(
                    "Маленькие радости за Геофрукты!\n"
                    "Выбирай товар и нажимай **Купить**."
                ),
                color=discord.Color.from_rgb(145, 190, 255),
            )
            embed.add_field(
                name="👛 Твой баланс",
                value=f"**{balance}** {economy.currency_word(balance)} 🫐",
                inline=False,
            )

            for item in SHOP_ITEMS:
                status = "" if item["enabled"] else "\n*Скоро в продаже*"
                price = f"**Цена: {item['price']} 🫐**" if item["enabled"] else "**Цена: 0 🫐**"
                embed.add_field(
                    name=f"{item['emoji']} {item['name']}",
                    value=f"{item['description']}\n{price}{status}",
                    inline=False,
                )

            embed.set_footer(text="Лавка Милки • Кнопки персональные и действуют 15 минут")
            view = GeoFruitShopView(interaction.user.id)
            await interaction.response.send_message(embed=embed, view=view)
            view.message = await interaction.original_response()


shop = GeoFruitShop()
