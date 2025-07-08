import discord
from discord.ext import commands
from discord.utils import get
import asyncio
from db_manager import db_manager
from config import MODERATOR_ID


class Tournaments(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.tournaments = {}  # Хранение данных о турнирах

    @commands.command(name="tournament")
    @commands.check(lambda ctx: ctx.author.id == MODERATOR_ID)
    async def create_tournament(self, ctx, name: str, slots: int):
        """Создает новый турнир (только для модераторов)"""
        try:
            if slots not in [8, 16, 32, 64]:
                return await ctx.send(
                    "❌ Число участников должно быть 8, 16, 32 или 64"
                )

            if name in self.tournaments:
                return await ctx.send("❌ Турнир с таким именем уже существует")

            # Создаем каналы
            tournament_data = await self.create_tournament_channels(ctx.guild, name)
            self.tournaments[name] = tournament_data

            # Настраиваем канал регистрации
            register_ch = tournament_data["register"]

            # Отправляем начальные сообщения
            participants_msg = await register_ch.send("**Участники турнира:**\nПусто")
            banned_msg = await register_ch.send("**Забаненные игроки:**\nПусто")
            blacklist_msg = await register_ch.send("**Черный список:**\nПусто")

            # Сохраняем ID сообщений
            self.tournaments[name]["participants_msg"] = participants_msg
            self.tournaments[name]["banned_msg"] = banned_msg
            self.tournaments[name]["blacklist_msg"] = blacklist_msg

            await ctx.send(
                f"✅ Турнир **{name}** создан! Регистрация в {register_ch.mention}"
            )

        except Exception as e:
            await ctx.send(f"❌ Ошибка: {str(e)}")
            print(f"[TOURNAMENT ERROR] {e}")

    async def create_tournament_channels(self, guild, name):
        """Создает ветку каналов для турнира"""
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            guild.me: discord.PermissionOverwrite(read_messages=True),
        }

        # Создаем категорию
        category = await guild.create_category(name, overwrites=overwrites)

        # Создаем каналы
        info_ch = await guild.create_text_channel(f"{name}-info", category=category)
        results_ch = await guild.create_text_channel(
            f"{name}-results", category=category
        )
        matches_ch = await guild.create_text_channel(
            f"{name}-matches", category=category
        )
        register_ch = await guild.create_text_channel(
            f"{name}-register", category=category
        )

        return {
            "category": category,
            "info": info_ch,
            "results": results_ch,
            "matches": matches_ch,
            "register": register_ch,
            "participants": [],
            "banned": [],
            "started": False,
        }

    @commands.Cog.listener()
    async def on_message(self, message):
        """Обработка регистрации на турнир"""
        if message.author.bot:
            return

        if not message.channel.name.endswith("-register"):
            return

        if not message.content.startswith(".register"):
            try:
                await message.delete()
            except:
                pass
            return

        await self.process_registration(message)

    async def process_registration(self, message):
        """Обрабатывает команду .register"""
        tournament_name = message.channel.name.replace("-register", "")

        if tournament_name not in self.tournaments:
            return

        user = message.author
        tournament = self.tournaments[tournament_name]

        # Проверка, что пользователь не зарегистрирован
        if any(p["id"] == user.id for p in tournament["participants"]):
            await message.add_reaction("❌")
            return await message.author.send("❌ Вы уже зарегистрированы!")

        # Проверка бана в боте
        player_data = db_manager.fetchone(
            "players",
            "SELECT playername, isbanned FROM players WHERE discordid = ?",
            (str(user.id),),
        )

        if not player_data or player_data[1] == 1:
            await message.add_reaction("❌")
            return await message.author.send("❌ Вы не верифицированы или забанены!")

        # Регистрация
        tournament["participants"].append(
            {"id": user.id, "name": player_data[0], "mention": user.mention}
        )

        await message.add_reaction("✅")
        await self.update_lists(tournament_name)

    async def update_lists(self, tournament_name):
        """Обновляет списки участников"""
        tournament = self.tournaments[tournament_name]

        participants = (
            "\n".join([p["mention"] for p in tournament["participants"]]) or "Пусто"
        )
        banned = "\n".join([f"<@{uid}>" for uid in tournament["banned"]]) or "Пусто"

        await tournament["participants_msg"].edit(
            content=f"**Участники турнира:**\n{participants}"
        )
        await tournament["banned_msg"].edit(content=f"**Забаненные игроки:**\n{banned}")


async def setup(bot):
    await bot.add_cog(Tournaments(bot))
