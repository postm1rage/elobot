import discord
from discord.ext import commands
from discord.utils import get
import asyncio
from db_manager import db_manager
from config import MODERATOR_ID


class Tournaments(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.tournaments = {}  # Кэш турниров {name: tournament_data}
        self.load_tournaments()

    def load_tournaments(self):
        """Загружает турниры из базы данных при старте"""
        tournaments = db_manager.fetchall(
            "tournaments", "SELECT id, name, slots, started FROM tournaments"
        )

        for t_id, name, slots, started in tournaments:
            # Получаем участников
            participants = db_manager.fetchall(
                "tournaments",
                """SELECT user_id, player_name FROM tournament_participants 
                WHERE tournament_id = ?""",
                (t_id,),
            )

            # Получаем баны
            banned = db_manager.fetchall(
                "tournaments",
                "SELECT user_id FROM tournament_bans WHERE tournament_id = ?",
                (t_id,),
            )

            self.tournaments[name] = {
                "id": t_id,
                "slots": slots,
                "started": bool(started),
                "participants": [
                    {"id": int(p[0]), "name": p[1], "mention": f"<@{p[0]}>"}
                    for p in participants
                ],
                "banned": [int(b[0]) for b in banned],
                "channels": {},  # Каналы будут заполнены при проверке
            }

    async def sync_tournament_channels(self, guild):
        """Синхронизирует каналы турниров с базой данных"""
        for category in guild.categories:
            if category.name in self.tournaments:
                channels = {
                    "category": category,
                    "info": discord.utils.get(
                        category.channels, name=f"{category.name}-info"
                    ),
                    "results": discord.utils.get(
                        category.channels, name=f"{category.name}-results"
                    ),
                    "matches": discord.utils.get(
                        category.channels, name=f"{category.name}-matches"
                    ),
                    "register": discord.utils.get(
                        category.channels, name=f"{category.name}-register"
                    ),
                }
                self.tournaments[category.name]["channels"] = channels

    @commands.Cog.listener()
    async def on_ready(self):
        """При запуске бота синхронизируем каналы"""
        for guild in self.bot.guilds:
            await self.sync_tournament_channels(guild)

    async def check_blacklist(self, user_id):
        """Проверяет, находится ли пользователь в черном списке"""
        result = db_manager.fetchone(
            "players",
            "SELECT isblacklisted FROM players WHERE discordid = ?",
            (str(user_id),),
        )
        return result and result[0] == 1

    @commands.command(name="tournament")
    @commands.check(lambda ctx: ctx.author.id == MODERATOR_ID)
    async def create_tournament(self, ctx, name: str, slots: int):
        """Создает новый турнир (только для модераторов)"""
        if slots not in [8, 16, 32, 64]:
            return await ctx.send("❌ Число участников должно быть 8, 16, 32 или 64")

        if name in self.tournaments:
            return await ctx.send("❌ Турнир с таким именем уже существует")

        # Создаем каналы
        try:
            tournament_data = await self.create_tournament_channels(ctx.guild, name)

            # Сохраняем в БД
            db_manager.execute(
                "tournaments",
                "INSERT INTO tournaments (name, slots) VALUES (?, ?)",
                (name, slots),
            )
            t_id = db_manager.get_lastrowid("tournaments")

            self.tournaments[name] = {
                "id": t_id,
                "slots": slots,
                "started": False,
                "participants": [],
                "banned": [],
                "channels": tournament_data,
                "participants_msg": None,
                "banned_msg": None,
                "blacklist_msg": None,
            }

            # Создаем информационные сообщения
            await self.update_lists(name)

            await ctx.send(
                f"✅ Турнир **{name}** создан! Регистрация в {tournament_data['register'].mention}"
            )
        except Exception as e:
            # Удаляем созданные каналы в случае ошибки
            for channel in tournament_data.values():
                if isinstance(channel, discord.abc.GuildChannel):
                    try:
                        await channel.delete()
                    except:
                        pass
            await ctx.send(f"❌ Ошибка при создании турнира: {str(e)}")

    async def create_tournament_channels(self, guild, name):
        """Создает ветку каналов для турнира"""
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            guild.me: discord.PermissionOverwrite(read_messages=True),
        }

        category = await guild.create_category(name, overwrites=overwrites)

        channels = {
            "info": await guild.create_text_channel(f"{name}-info", category=category),
            "results": await guild.create_text_channel(
                f"{name}-results", category=category
            ),
            "matches": await guild.create_text_channel(
                f"{name}-matches", category=category
            ),
            "register": await guild.create_text_channel(
                f"{name}-register", category=category
            ),
        }

        return {"category": category, **channels}

    @commands.command()
    @commands.check(lambda ctx: ctx.author.id == MODERATOR_ID)
    async def tban(self, ctx, member: discord.Member):
        """Бан игрока в текущем турнире"""
        tournament_name = ctx.channel.category.name

        if tournament_name not in self.tournaments:
            return await ctx.send("❌ Это не турнирный канал")

        if member.id in self.tournaments[tournament_name]["banned"]:
            return await ctx.send("❌ Игрок уже забанен в этом турнире")

        # Добавляем в базу
        db_manager.execute(
            "tournaments",
            """INSERT INTO tournament_bans (tournament_id, user_id)
            VALUES (?, ?)""",
            (self.tournaments[tournament_name]["id"], str(member.id)),
        )

        self.tournaments[tournament_name]["banned"].append(member.id)

        # Удаляем из участников если был зарегистрирован
        if any(
            p["id"] == member.id
            for p in self.tournaments[tournament_name]["participants"]
        ):
            db_manager.execute(
                "tournaments",
                """DELETE FROM tournament_participants 
                WHERE tournament_id = ? AND user_id = ?""",
                (self.tournaments[tournament_name]["id"], str(member.id)),
            )
            self.tournaments[tournament_name]["participants"] = [
                p
                for p in self.tournaments[tournament_name]["participants"]
                if p["id"] != member.id
            ]
            await self.update_lists(tournament_name)

        await ctx.send(f"✅ {member.mention} забанен в турнире")
        await self.clean_user_messages(member.id, ctx.channel.category)

    @commands.command()
    @commands.check(lambda ctx: ctx.author.id == MODERATOR_ID)
    async def untban(self, ctx, member: discord.Member):
        """Разбан игрока в текущем турнире"""
        tournament_name = ctx.channel.category.name

        if tournament_name not in self.tournaments:
            return await ctx.send("❌ Это не турнирный канал")

        if member.id not in self.tournaments[tournament_name]["banned"]:
            return await ctx.send("❌ Игрок не забанен в этом турнире")

        # Удаляем из базы
        db_manager.execute(
            "tournaments",
            """DELETE FROM tournament_bans 
            WHERE tournament_id = ? AND user_id = ?""",
            (self.tournaments[tournament_name]["id"], str(member.id)),
        )

        self.tournaments[tournament_name]["banned"].remove(member.id)
        await ctx.send(f"✅ {member.mention} разбанен в турнире")

    @commands.command()
    @commands.check(lambda ctx: ctx.author.id == MODERATOR_ID)
    async def blacklist(self, ctx, member: discord.Member):
        """Добавить игрока в черный список турниров"""
        if await self.check_blacklist(member.id):
            return await ctx.send("❌ Игрок уже в черном списке")

        db_manager.execute(
            "players",
            "UPDATE players SET isblacklisted = 1 WHERE discordid = ?",
            (str(member.id),),
        )

        # Удаляем из всех текущих турниров
        for name, tournament in self.tournaments.items():
            if any(p["id"] == member.id for p in tournament["participants"]):
                db_manager.execute(
                    "tournaments",
                    """DELETE FROM tournament_participants 
                    WHERE tournament_id = ? AND user_id = ?""",
                    (tournament["id"], str(member.id)),
                )
                tournament["participants"] = [
                    p for p in tournament["participants"] if p["id"] != member.id
                ]
                await self.update_lists(name)

            if member.id not in tournament["banned"]:
                db_manager.execute(
                    "tournaments",
                    """INSERT INTO tournament_bans (tournament_id, user_id)
                    VALUES (?, ?)""",
                    (tournament["id"], str(member.id)),
                )
                tournament["banned"].append(member.id)
                await self.update_lists(name)

        await ctx.send(f"✅ {member.mention} добавлен в черный список турниров")
        await self.clean_user_messages(member.id)

    @commands.command()
    @commands.check(lambda ctx: ctx.author.id == MODERATOR_ID)
    async def unblacklist(self, ctx, member: discord.Member):
        """Удалить игрока из черного списка турниров"""
        if not await self.check_blacklist(member.id):
            return await ctx.send("❌ Игрок не в черном списке")

        db_manager.execute(
            "players",
            "UPDATE players SET isblacklisted = 0 WHERE discordid = ?",
            (str(member.id),),
        )
        await ctx.send(f"✅ {member.mention} удален из черного списка")

    async def clean_user_messages(self, user_id, category=None):
        """Удаляет сообщения пользователя в турнирных каналах"""
        targets = (
            [category]
            if category
            else [
                t["channels"]["category"]
                for t in self.tournaments.values()
                if "channels" in t and t["channels"].get("category")
            ]
        )

        for target in targets:
            for channel in target.channels:
                try:
                    async for message in channel.history(limit=100):
                        if message.author.id == user_id:
                            await message.delete()
                            await asyncio.sleep(0.5)
                except:
                    continue

    @commands.command(name="unregister")
    @commands.check(lambda ctx: ctx.channel.name.endswith("-register"))
    async def unregister(self, ctx):
        """Позволяет игроку отменить регистрацию на турнир"""
        tournament_name = ctx.channel.name.replace("-register", "")
        user = ctx.author

        if tournament_name not in self.tournaments:
            await ctx.message.add_reaction("❌")
            return await ctx.send("❌ Этот турнир не найден", delete_after=5)

        tournament = self.tournaments[tournament_name]

        # Проверяем, зарегистрирован ли игрок
        player_index = next(
            (i for i, p in enumerate(tournament["participants"]) if p["id"] == user.id),
            None,
        )

        if player_index is None:
            await ctx.message.add_reaction("❌")
            return await ctx.send(
                "❌ Вы не зарегистрированы в этом турнире", delete_after=5
            )

        # Удаляем игрока из базы
        db_manager.execute(
            "tournaments",
            """DELETE FROM tournament_participants 
            WHERE tournament_id = ? AND user_id = ?""",
            (tournament["id"], str(user.id)),
        )

        # Удаляем из списка участников
        tournament["participants"].pop(player_index)
        await ctx.message.add_reaction("✅")
        await ctx.send(f"✅ Вы успешно отменили регистрацию на турнир", delete_after=5)
        await self.update_lists(tournament_name)

    async def update_lists(self, tournament_name):
        """Обновляет списки участников и забаненных"""
        tournament = self.tournaments[tournament_name]
        channels = tournament.get("channels", {})

        # Форматируем списки
        participants = (
            "\n".join(
                f"{i+1}. {p['mention']} ({p['name']})"
                for i, p in enumerate(tournament["participants"])
            )
            or "Пусто"
        )

        banned = "\n".join(f"<@{uid}>" for uid in tournament["banned"]) or "Пусто"

        # Получаем черный список из БД
        blacklisted = db_manager.fetchall(
            "players", "SELECT discordid FROM players WHERE isblacklisted = 1"
        )
        blacklist_mentions = "\n".join(f"<@{row[0]}>" for row in blacklisted) or "Пусто"

        # Создаем или обновляем сообщения
        if not tournament.get("participants_msg"):
            tournament["participants_msg"] = await channels["info"].send(
                f"**Участники турнира ({len(tournament['participants'])}/{tournament['slots']}):**\n{participants}"
            )
        else:
            await tournament["participants_msg"].edit(
                content=f"**Участники турнира ({len(tournament['participants'])}/{tournament['slots']}):**\n{participants}"
            )

        if not tournament.get("banned_msg"):
            tournament["banned_msg"] = await channels["info"].send(
                f"**Забаненные игроки:**\n{banned}"
            )
        else:
            await tournament["banned_msg"].edit(
                content=f"**Забаненные игроки:**\n{banned}"
            )

        if not tournament.get("blacklist_msg"):
            tournament["blacklist_msg"] = await channels["info"].send(
                f"**Черный список:**\n{blacklist_mentions}"
            )
        else:
            await tournament["blacklist_msg"].edit(
                content=f"**Черный список:**\n{blacklist_mentions}"
            )

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return

        if not message.channel.name.endswith("-register"):
            return

        # Удаляем все сообщения кроме команд register/unregister
        if not message.content.startswith((".register", ".unregister")):
            try:
                await message.delete()
            except:
                pass
            return

        # Обрабатываем команды
        if message.content.startswith(".register"):
            await self.process_registration(message)
        elif message.content.startswith(".unregister"):
            ctx = await self.bot.get_context(message)
            await self.unregister(ctx)

    async def process_registration(self, message):
        """Обрабатывает регистрацию на турнир"""
        tournament_name = message.channel.name.replace("-register", "")
        user = message.author

        if tournament_name not in self.tournaments:
            return

        tournament = self.tournaments[tournament_name]

        if tournament.get("started", False):
            await message.add_reaction("❌")
            return await user.send("❌ Регистрация закрыта - турнир уже начался")

        # Проверка условий
        checks = {
            "not_in_db": not self.is_user_verified(user.id),
            "blacklisted": await self.check_blacklist(user.id),
            "banned": user.id in tournament["banned"],
            "registered": any(p["id"] == user.id for p in tournament["participants"]),
            "globally_banned": self.is_user_globally_banned(user.id),
        }

        if any(checks.values()):
            reason = (
                "не найден в базе игроков"
                if checks["not_in_db"]
                else (
                    "в черном списке"
                    if checks["blacklisted"]
                    else (
                        "забанен в этом турнире"
                        if checks["banned"]
                        else (
                            "уже зарегистрирован"
                            if checks["registered"]
                            else "забанен глобально"
                        )
                    )
                )
            )
            await message.add_reaction("❌")
            try:
                await user.send(f"❌ Регистрация отклонена: {reason}")
            except:
                pass
            return

        # Регистрация
        player_name = db_manager.fetchone(
            "players",
            "SELECT playername FROM players WHERE discordid = ?",
            (str(user.id),),
        )[0]

        try:
            db_manager.execute(
                "tournaments",
                """INSERT INTO tournament_participants 
                (tournament_id, user_id, player_name) 
                VALUES (?, ?, ?)""",
                (tournament["id"], str(user.id), player_name),
            )

            tournament["participants"].append(
                {"id": user.id, "name": player_name, "mention": user.mention}
            )

            await message.add_reaction("✅")
            await self.update_lists(tournament_name)

            # Проверка набора участников
            if len(tournament["participants"]) >= tournament["slots"]:
                await tournament["channels"]["register"].send(
                    "🎉 Набрано достаточное количество участников! "
                    "Модератор может начать турнир командой `.tstart`"
                )

        except Exception as e:
            await message.add_reaction("❌")
            await user.send(f"❌ Ошибка при регистрации: {str(e)}")

    def is_user_verified(self, user_id):
        """Проверяет, есть ли игрок в базе (независимо от бана)"""
        result = db_manager.fetchone(
            "players",
            "SELECT 1 FROM players WHERE discordid = ?",
            (str(user_id),),
        )
        return result is not None

    def is_user_globally_banned(self, user_id):
        """Проверяет глобальный бан игрока"""
        result = db_manager.fetchone(
            "players",
            "SELECT isbanned FROM players WHERE discordid = ?",
            (str(user_id),),
        )
        return result and result[0] == 1


async def setup(bot):
    await bot.add_cog(Tournaments(bot))
