import discord
from discord.ext import commands
from discord.utils import get
import asyncio
from db_manager import db_manager
from config import MODERATOR_ID, MODES, MODE_NAMES
from queueing import create_match
from datetime import datetime


class Tournaments(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.tournaments = {}
        self.load_tournaments()

    def load_tournaments(self):
        """Загружает турниры из базы данных при старте"""
        tournaments = db_manager.fetchall(
            "tournaments", "SELECT id, name, slots, started FROM tournaments"
        )

        for t_id, name, slots, started in tournaments:
            participants = db_manager.fetchall(
                "tournaments",
                """SELECT user_id, player_name FROM tournament_participants 
                WHERE tournament_id = ?""",
                (t_id,),
            )

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
                "channels": {},
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

                # Восстанавливаем сообщения со списками
                async for message in channels["info"].history(limit=10):
                    if "Участники турнира" in message.content:
                        self.tournaments[category.name]["participants_msg"] = message
                    elif "Забаненные игроки" in message.content:
                        self.tournaments[category.name]["banned_msg"] = message
                    elif "Черный список" in message.content:
                        self.tournaments[category.name]["blacklist_msg"] = message

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

    async def update_all_blacklists(self):
        """Обновляет сообщения с черным списком во всех турнирах"""
        blacklisted = db_manager.fetchall(
            "players", "SELECT discordid FROM players WHERE isblacklisted = 1"
        )
        blacklist_mentions = "\n".join(f"<@{row[0]}>" for row in blacklisted) or "Пусто"

        for name, tournament in self.tournaments.items():
            if "channels" in tournament and "info" in tournament["channels"]:
                if "blacklist_msg" in tournament and tournament["blacklist_msg"]:
                    try:
                        await tournament["blacklist_msg"].edit(
                            content=f"**Черный список:**\n{blacklist_mentions}"
                        )
                    except discord.NotFound:
                        tournament["blacklist_msg"] = await tournament["channels"][
                            "info"
                        ].send(f"**Черный список:**\n{blacklist_mentions}")

    async def update_all_banned_lists(self):
        """Обновляет списки забаненных во всех турнирах"""
        for name, tournament in self.tournaments.items():
            banned = "\n".join(f"<@{uid}>" for uid in tournament["banned"]) or "Пусто"
            if "channels" in tournament and "info" in tournament["channels"]:
                if "banned_msg" in tournament and tournament["banned_msg"]:
                    try:
                        await tournament["banned_msg"].edit(
                            content=f"**Забаненные игроки:**\n{banned}"
                        )
                    except discord.NotFound:
                        tournament["banned_msg"] = await tournament["channels"][
                            "info"
                        ].send(f"**Забаненные игроки:**\n{banned}")

    @commands.command(name="tournament")
    @commands.check(lambda ctx: ctx.author.id == MODERATOR_ID)
    async def create_tournament(self, ctx, name: str, slots: int):
        """Создает новый турнир (только для модераторов)"""
        if slots not in [8, 16, 32, 64]:
            return

        if name in self.tournaments:
            return

        try:
            tournament_data = await self.create_tournament_channels(ctx.guild, name)

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
            }

            # Создаем информационные сообщения
            await self.update_lists(name)

        except Exception as e:
            # Удаляем созданные каналы в случае ошибки
            for key, channel in tournament_data.items():
                if key != "category" and isinstance(channel, discord.abc.GuildChannel):
                    try:
                        await channel.delete()
                    except:
                        pass
            if "category" in tournament_data:
                try:
                    await tournament_data["category"].delete()
                except:
                    pass

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
    async def tstart(self, ctx):
        """Начинает турнир (только для модераторов)"""
        tournament_name = ctx.channel.category.name
        
        if tournament_name not in self.tournaments:
            return await ctx.send("❌ Турнир не найден")
        
        tournament = self.tournaments[tournament_name]
        
        if tournament["started"]:
            return await ctx.send("❌ Турнир уже начат")
        
        participants = tournament["participants"]
        slots = tournament["slots"]
        
        # Заполняем пустые слоты
        while len(participants) < slots:
            empty_slot = {
                "id": 0,
                "name": f"emptyslot{len(participants)+1}",
                "mention": "Пустой слот"
            }
            participants.append(empty_slot)
        
        # Обновляем статус турнира в БД
        db_manager.execute(
            "tournaments",
            "UPDATE tournaments SET started = 1 WHERE id = ?",
            (tournament["id"],)
        )
        
        tournament["started"] = True
        
        # Создаем матчи первого раунда
        await self.create_first_round(tournament)
        
        await ctx.send("✅ Турнир начат! Матчи первого раунда созданы.")

    async def create_first_round(self, tournament):
        """Создает матчи первого раунда турнира"""
        participants = tournament["participants"]
        
        # Сортируем участников по рейтингу
        rated_participants = []
        for p in participants:
            if p["id"] == 0:  # Пустой слот
                rating = 0
            else:
                # Проверяем, что игрок не участвует в другом турнирном матче
                active_tournament_match = db_manager.fetchone(
                    'matches',
                    """
                    SELECT 1 FROM matches 
                    WHERE (player1 = ? OR player2 = ?) 
                    AND isover = 0 
                    AND matchtype = 2
                    """,
                    (p["name"], p["name"]),
                )
                
                if active_tournament_match:
                    continue  # Пропускаем игрока, если он уже в турнирном матче
                
                rating = db_manager.fetchone(
                    "players",
                    "SELECT currentelo FROM players WHERE discordid = ?",
                    (str(p["id"]),)
                )
                rating = rating[0] if rating else 1000
            
            rated_participants.append((rating, p))
        
        # Сортируем по рейтингу (лучшие первые)
        rated_participants.sort(reverse=True, key=lambda x: x[0])
        
        # Разбиваем на пары (1 vs последний, 2 vs предпоследний и т.д.)
        matches = []
        for i in range(len(rated_participants) // 2):
            player1 = rated_participants[i][1]
            player2 = rated_participants[len(rated_participants)-1-i][1]
            
            # Создаем матч только если оба не пустые слоты
            if player1["id"] != 0 or player2["id"] != 0:
                matches.append((player1, player2))
        
        # Создаем матчи
        for player1, player2 in matches:
            # Для реальных игроков получаем данные из базы
            p1_data = {
                "discord_id": player1["id"],
                "nickname": player1["name"],
                "rating": db_manager.fetchone(
                    "players",
                    "SELECT currentelo FROM players WHERE discordid = ?",
                    (str(player1["id"]),)
                )[0] if player1["id"] != 0 else 0,
                "channel_id": tournament["channels"]["matches"].id,
                "join_time": datetime.now()
            }
            
            p2_data = {
                "discord_id": player2["id"],
                "nickname": player2["name"],
                "rating": db_manager.fetchone(
                    "players",
                    "SELECT currentelo FROM players WHERE discordid = ?",
                    (str(player2["id"]),)
                )[0] if player2["id"] != 0 else 0,
                "channel_id": tournament["channels"]["matches"].id,
                "join_time": datetime.now()
            }
            
            # Создаем турнирный матч (matchtype=2)
            await create_match(
                MODES["station5f"],  # Турниры всегда в Station 5 flags
                p1_data,
                p2_data,
                matchtype=2,
                tournament_id=tournament["id"]
            )

    @commands.command()
    @commands.check(lambda ctx: ctx.author.id == MODERATOR_ID)
    async def tban(self, ctx, member: discord.Member):
        """Бан игрока в текущем турнире"""
        tournament_name = ctx.channel.category.name

        if tournament_name not in self.tournaments:
            return

        if member.id in self.tournaments[tournament_name]["banned"]:
            return

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

        await self.clean_user_messages(member.id, ctx.channel.category)

        # Обновляем списки
        await self.update_lists(tournament_name)
        await self.update_all_banned_lists()

    @commands.command()
    @commands.check(lambda ctx: ctx.author.id == MODERATOR_ID)
    async def untban(self, ctx, member: discord.Member):
        """Разбан игрока в текущем турнире"""
        tournament_name = ctx.channel.category.name

        if tournament_name not in self.tournaments:
            return

        if member.id not in self.tournaments[tournament_name]["banned"]:
            return

        # Удаляем из базы
        db_manager.execute(
            "tournaments",
            """DELETE FROM tournament_bans 
            WHERE tournament_id = ? AND user_id = ?""",
            (self.tournaments[tournament_name]["id"], str(member.id)),
        )

        self.tournaments[tournament_name]["banned"].remove(member.id)

        # Обновляем списки
        await self.update_lists(tournament_name)
        await self.update_all_banned_lists()

    @commands.command()
    @commands.check(lambda ctx: ctx.author.id == MODERATOR_ID)
    async def blacklist(self, ctx, member: discord.Member):
        """Добавить игрока в черный список турниров"""
        if await self.check_blacklist(member.id):
            return

        db_manager.execute(
            "players",
            "UPDATE players SET isblacklisted = 1 WHERE discordid = ?",
            (str(member.id),),
        )

        # Удаляем из всех текущих турниров
        for name, tournament in self.tournaments.items():
            # Удаляем из участников
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

            # Добавляем в баны
            if member.id not in tournament["banned"]:
                db_manager.execute(
                    "tournaments",
                    """INSERT INTO tournament_bans (tournament_id, user_id)
                    VALUES (?, ?)""",
                    (tournament["id"], str(member.id)),
                )
                tournament["banned"].append(member.id)

        await self.clean_user_messages(member.id)

        # Обновляем все списки
        for name in self.tournaments:
            await self.update_lists(name)
        await self.update_all_blacklists()
        await self.update_all_banned_lists()

    @commands.command()
    @commands.check(lambda ctx: ctx.author.id == MODERATOR_ID)
    async def unblacklist(self, ctx, member: discord.Member):
        """Удалить игрока из черного списка турниров"""
        if not await self.check_blacklist(member.id):
            return

        db_manager.execute(
            "players",
            "UPDATE players SET isblacklisted = 0 WHERE discordid = ?",
            (str(member.id),),
        )

        # Снимаем бан в текущем турнире
        if ctx.channel.category and ctx.channel.category.name in self.tournaments:
            tournament_name = ctx.channel.category.name
            tournament = self.tournaments[tournament_name]

            if member.id in tournament["banned"]:
                db_manager.execute(
                    "tournaments",
                    """DELETE FROM tournament_bans 
                    WHERE tournament_id = ? AND user_id = ?""",
                    (tournament["id"], str(member.id)),
                )
                tournament["banned"].remove(member.id)
                await self.update_lists(tournament_name)

        # Обновляем все списки черного списка
        await self.update_all_blacklists()

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
            return

        tournament = self.tournaments[tournament_name]

        # Проверяем, зарегистрирован ли игрок
        player_index = next(
            (i for i, p in enumerate(tournament["participants"]) if p["id"] == user.id),
            None,
        )

        if player_index is None:
            return

        # Удаляем игрока из базы
        db_manager.execute(
            "tournaments",
            """DELETE FROM tournament_participants 
            WHERE tournament_id = ? AND user_id = ?""",
            (tournament["id"], str(user.id)),
        )

        # Удаляем из списка участников
        tournament["participants"].pop(player_index)
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
        if "info" in channels:
            if not tournament.get("participants_msg"):
                tournament["participants_msg"] = await channels["info"].send(
                    f"**Участники турнира ({len(tournament['participants'])}/{tournament['slots']}):**\n{participants}"
                )
            else:
                try:
                    await tournament["participants_msg"].edit(
                        content=f"**Участники турнира ({len(tournament['participants'])}/{tournament['slots']}):**\n{participants}"
                    )
                except discord.NotFound:
                    tournament["participants_msg"] = await channels["info"].send(
                        f"**Участники турнира ({len(tournament['participants'])}/{tournament['slots']}):**\n{participants}"
                    )

            if not tournament.get("banned_msg"):
                tournament["banned_msg"] = await channels["info"].send(
                    f"**Забаненные игроки:**\n{banned}"
                )
            else:
                try:
                    await tournament["banned_msg"].edit(
                        content=f"**Забаненные игроки:**\n{banned}"
                    )
                except discord.NotFound:
                    tournament["banned_msg"] = await channels["info"].send(
                        f"**Забаненные игроки:**\n{banned}"
                    )

            if not tournament.get("blacklist_msg"):
                tournament["blacklist_msg"] = await channels["info"].send(
                    f"**Черный список:**\n{blacklist_mentions}"
                )
            else:
                try:
                    await tournament["blacklist_msg"].edit(
                        content=f"**Черный список:**\n{blacklist_mentions}"
                    )
                except discord.NotFound:
                    tournament["blacklist_msg"] = await channels["info"].send(
                        f"**Черный список:**\n{blacklist_mentions}"
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
            return  # Регистрация закрыта

        # Проверка условий
        checks = {
            "not_in_db": not self.is_user_verified(user.id),
            "blacklisted": await self.check_blacklist(user.id),
            "banned": user.id in tournament["banned"],
            "registered": any(p["id"] == user.id for p in tournament["participants"]),
            "globally_banned": self.is_user_globally_banned(user.id),
        }

        if any(checks.values()):
            return  # Регистрация отклонена

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

            await self.update_lists(tournament_name)

            # Проверка набора участников
            if len(tournament["participants"]) >= tournament["slots"]:
                await tournament["channels"]["register"].send(
                    "🎉 Набрано достаточное количество участников! "
                    "Модератор может начать турнир командой `.tstart`"
                )

        except Exception:
            pass

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
