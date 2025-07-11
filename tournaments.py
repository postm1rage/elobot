import discord
from discord.ext import commands
from discord.utils import get
import asyncio
from db_manager import db_manager
from config import MODERATOR_ID, MODES, MODE_NAMES
from queueing import create_match
from datetime import datetime
from tour import Tour


class Tournaments(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.tournaments = {}
        self.active_tours = {}
        self.load_tournaments()

    async def load_active_tours(self):
        """Загружает активные турниры из базы данных"""
        active_tours = db_manager.fetchall(
            "tournaments",
            """SELECT t.name, t.slots, a.current_round, a.participants, a.winners, a.matches
            FROM tournaments t
            JOIN active_tours a ON t.id = a.tournament_id
            WHERE t.started = 1""",
        )

        for name, slots, current_round, participants, winners, matches in active_tours:
            import json

            tour = Tour(
                bot=self.bot,
                tournament_name=name,
                participants=json.loads(participants),
                slots=slots,
                cog=self,
            )

            # Вручную устанавливаем состояние
            tour.current_round = current_round
            tour.winners = json.loads(winners)
            tour.matches = []
            for m in json.loads(matches):
                tour.matches.append(
                    {
                        "id": m["id"],
                        "player1": m["player1"],
                        "player2": m["player2"],
                        "winner": m["winner"],
                        "is_finished": m["is_finished"],
                    }
                )

            self.active_tours[name] = tour
            print(f"Восстановлен активный турнир: {name} (тур {current_round})")

    @commands.Cog.listener()
    async def on_ready(self):
        """При запуске бота загружаем активные турниры"""
        for guild in self.bot.guilds:
            await self.sync_tournament_channels(guild)

        await self.load_active_tours()  # Восстанавливаем активные турниры
        self.bot.loop.create_task(self.periodic_tournament_check())

    def load_tournaments(self):
        """Загружает турниры из базы данных при старте"""
        tournaments = db_manager.fetchall(
            "tournaments", 
            "SELECT id, name, slots, started, currentplayers FROM tournaments"
        )

        for t_id, name, slots, started, currentplayers in tournaments:
            # Загружаем участников как список ников
            participants = currentplayers.split() if currentplayers else []
            
            # Загружаем баны
            banned = db_manager.fetchall(
                "tournaments",
                "SELECT user_id FROM tournament_bans WHERE tournament_id = ?",
                (t_id,),
            )
            banned = [int(b[0]) for b in banned]

            self.tournaments[name] = {
                "id": t_id,
                "slots": slots,
                "started": bool(started),
                "participants": participants,  # Список ников
                "banned": banned,
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
            self.bot.loop.create_task(self.periodic_tournament_check())

    async def periodic_tournament_check(self):
        while True:
            await asyncio.sleep(60)  # Проверка каждую минуту
            for tour in list(self.active_tours.values()):
                await tour.check_round_completion()

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
            await ctx.send("❌ Недопустимое количество слотов. Допустимые значения: 8, 16, 32, 64.")
            return

        if name in self.tournaments:
            await ctx.send("❌ Турнир с таким именем уже существует.")
            return

        try:
            tournament_data = await self.create_tournament_channels(ctx.guild, name)

            # Создаем запись в базе
            db_manager.execute(
                "tournaments",
                "INSERT INTO tournaments (name, slots) VALUES (?, ?)",
                (name, slots),
            )
            t_id = db_manager.get_lastrowid("tournaments")

            # Инициализируем в памяти
            self.tournaments[name] = {
                "id": t_id,
                "slots": slots,
                "started": False,
                "participants": [],   # Пустой список участников
                "banned": [],
                "channels": tournament_data,
            }

            # Создаем информационные сообщения
            await self.update_lists(name)

            await ctx.send(f"✅ Турнир **{name}** создан! Участники могут регистрироваться в канале {tournament_data['register'].mention}")

        except Exception as e:
            print(f"Ошибка при создании турнира: {e}")
            # Удаляем созданные каналы в случае ошибки
            for key, channel in tournament_data.items():
                if key != 'category' and isinstance(channel, discord.abc.GuildChannel):
                    try:
                        await channel.delete()
                    except:
                        pass
            if 'category' in tournament_data:
                try:
                    await tournament_data['category'].delete()
                except:
                    pass
            await ctx.send(f"❌ Произошла ошибка при создании турнира: {e}")

    @commands.command()
    @commands.check(lambda ctx: ctx.author.id == MODERATOR_ID)
    async def nexttour(self, ctx):
        """Принудительно начинает следующий тур в текущем туре (только для модераторов)"""
        tournament_name = ctx.channel.category.name

        if tournament_name not in self.active_tours:
            return await ctx.send("❌ Активный турнир не найден или не начат")

        tour = self.active_tours[tournament_name]

        # Проверяем, все ли матчи текущего тура завершены
        unfinished_matches = db_manager.fetchall(
            "matches",
            """SELECT matchid FROM matches 
            WHERE tournament_id = ? AND isover = 0""",
            (tournament_name,),
        )

        if unfinished_matches:
            return await ctx.send(
                f"❌ Не все матчи текущего тура завершены. Осталось: {len(unfinished_matches)}"
            )

        # Если все матчи завершены, переходим к следующему туру
        if len(tour.winners) == 1:
            return await ctx.send("❌ Турнир уже завершен, есть победитель")

        await ctx.send("⏳ Принудительно начинаю следующий тур...")
        await tour.check_round_completion()
        await ctx.send(f"✅ Тур {tour.current_round} начат!")

    async def create_tournament_channels(self, guild, name):
        """Создает публичные каналы для турнира"""
        # Создаем категорию с стандартными правами (публичную)
        category = await guild.create_category(name)

        # Создаем текстовые каналы с стандартными правами
        channels = {
            "info": await guild.create_text_channel(
                f"{name}-info", category=category, topic=f"Информация о турнире {name}"
            ),
            "results": await guild.create_text_channel(
                f"{name}-results", category=category, topic=f"Результаты турнира {name}"
            ),
            "matches": await guild.create_text_channel(
                f"{name}-matches", category=category, topic=f"Турнирные матчи {name}"
            ),
            "register": await guild.create_text_channel(
                f"{name}-register",
                category=category,
                topic=f"Регистрация на турнир {name}",
            ),
        }

        return {"category": category, **channels}

    @commands.command()
    @commands.check(lambda ctx: ctx.author.id == MODERATOR_ID)
    async def tstart(self, ctx):
        tournament_name = ctx.channel.category.name
        if tournament_name not in self.tournaments:
            await ctx.send("❌ Турнир не найден")
            return
        tournament = self.tournaments[tournament_name]
        if tournament.get("started", False):
            await ctx.send("❌ Турнир уже начат")
            return

        # Проверяем, что участников достаточно
        if len(tournament["participants"]) < tournament["slots"]:
            await ctx.send(f"❌ Недостаточно участников. Зарегистрировано: { len(tournament['participants'])}/{tournament['slots']}")
            return

        # Создаем экземпляр Tour, передавая список ников участников
        self.active_tours[tournament_name] = Tour(
            bot=self.bot,
            tournament_name=tournament_name,
            participants=tournament["participants"],  # Список ников
            slots=tournament["slots"],
            cog=self,
        )

        # Помечаем турнир как начатый в базе
        db_manager.execute(
            "tournaments",
            "UPDATE tournaments SET started = 1 WHERE id = ?",
            (tournament["id"],),
        )
        tournament["started"] = True

        # Начинаем первый тур
        await self.active_tours[tournament_name].start_round()
        await ctx.send("✅ Турнир начат! Первый тур создан.")

    @commands.command()
    @commands.check(lambda ctx: ctx.author.id == MODERATOR_ID)
    async def setwinner(self, ctx, match_id: int, winner_name: str):
        try:
            # Получаем полные данные о матче
            match_data = db_manager.fetchone(
                "matches",
                """SELECT player1, player2, tournament_id, isover 
                FROM matches 
                WHERE matchid = ? AND matchtype = 2""",
                (match_id,),
            )

            if not match_data:
                return await ctx.send(
                    f"❌ Матч с ID {match_id} не найден или не является турнирным"
                )

            player1, player2, tournament_id, isover = match_data

            if isover == 1:
                return await ctx.send("❌ Этот матч уже завершен")

            if winner_name not in [player1, player2]:
                return await ctx.send(
                    f"❌ Игрок {winner_name} не участвовал в матче {match_id}"
                )

            # Получаем название турнира (даже если tournament_id None)
            tournament_name = "Неизвестный турнир"
            if tournament_id:
                tournament_data = db_manager.fetchone(
                    "tournaments",
                    "SELECT name FROM tournaments WHERE id = ?",
                    (tournament_id,),
                )
                if tournament_data:
                    tournament_name = tournament_data[0] or "Неизвестный турнир"

            # Определяем счет
            if winner_name == player1:
                score1, score2 = 1, 0
                loser_name = player2
            else:
                score1, score2 = 0, 1
                loser_name = player1

            # Обновляем матч в базе
            db_manager.execute(
                "matches",
                """UPDATE matches 
                SET player1score = ?, player2score = ?, isover = 1, isverified = 1 
                WHERE matchid = ?""",
                (score1, score2, match_id),
            )

            # Обновляем статистику (без ELO для турнирных матчей)
            db_manager.execute(
                "players",
                "UPDATE players SET wins = wins + 1 WHERE playername = ?",
                (winner_name,),
            )
            db_manager.execute(
                "players",
                "UPDATE players SET losses = losses + 1 WHERE playername = ?",
                (loser_name,),
            )

            # Отправляем подтверждение
            embed = discord.Embed(
                title="✅ Победитель установлен",
                description=f"В матче #{match_id} турнира **{tournament_name}**",
                color=discord.Color.green(),
            )
            embed.add_field(name="Победитель", value=winner_name, inline=True)
            embed.add_field(name="Проигравший", value=loser_name, inline=True)
            embed.add_field(name="Счет", value=f"{score1}-{score2}", inline=False)
            await ctx.send(embed=embed)

            # Обновляем турнирный прогресс
            if tournament_name in self.active_tours:
                await self.active_tours[tournament_name].check_round_completion()
            else:
                # Попробуем найти турнир по имени, если не нашли по ID
                for tour_name, tour in self.active_tours.items():
                    if any(m["id"] == match_id for m in tour.matches):
                        await tour.check_round_completion()
                        break

            # Отправляем результат в канал результатов турнира
            results_channel = discord.utils.get(
                ctx.guild.channels, name=f"{tournament_name}-results"
            )
            if results_channel:
                result_embed = discord.Embed(
                    title=f"🏆 Турнирный матч завершен | ID: {match_id}",
                    description=(
                        f"**Турнир:** {tournament_name}\n"
                        f"**Игроки:** {player1} vs {player2}\n"
                        f"**Счет:** {score1}-{score2}\n"
                        f"**Победитель:** {winner_name}"
                    ),
                    color=discord.Color.green(),
                )
                await results_channel.send(embed=result_embed)

        except Exception as e:
            print(f"Ошибка в setwinner: {e}")
            await ctx.send("❌ Произошла ошибка при обработке команды")

    async def create_first_round(self, tournament):
        """Создает матчи первого тура турнира"""
        participants = tournament["participants"]

        # Сортируем участников по рейтингу
        rated_participants = []
        for p in participants:
            if p["id"] == 0:  # Пустой слот
                rating = 0
            else:
                # Проверяем, что игрок не участвует в другом турнирном матче
                active_tournament_match = db_manager.fetchone(
                    "matches",
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
                    (str(p["id"]),),
                )
                rating = rating[0] if rating else 1000

            rated_participants.append((rating, p))

        # Сортируем по рейтингу (лучшие первые)
        rated_participants.sort(reverse=True, key=lambda x: x[0])

        # Разбиваем на пары (1 vs последний, 2 vs предпоследний и т.д.)
        matches = []
        for i in range(len(rated_participants) // 2):
            player1 = rated_participants[i][1]
            player2 = rated_participants[len(rated_participants) - 1 - i][1]

            # Создаем матч только если оба не пустые слоты
            if player1["id"] != 0 or player2["id"] != 0:
                matches.append((player1, player2))

        # Создаем матчи
        for player1, player2 in matches:
            # Для реальных игроков получаем данные из базы
            p1_data = {
                "discord_id": player1["id"],
                "nickname": player1["name"],
                "rating": (
                    db_manager.fetchone(
                        "players",
                        "SELECT currentelo FROM players WHERE discordid = ?",
                        (str(player1["id"]),),
                    )[0]
                    if player1["id"] != 0
                    else 0
                ),
                "channel_id": tournament["channels"]["matches"].id,
                "join_time": datetime.now(),
            }

            p2_data = {
                "discord_id": player2["id"],
                "nickname": player2["name"],
                "rating": (
                    db_manager.fetchone(
                        "players",
                        "SELECT currentelo FROM players WHERE discordid = ?",
                        (str(player2["id"]),),
                    )[0]
                    if player2["id"] != 0
                    else 0
                ),
                "channel_id": tournament["channels"]["matches"].id,
                "join_time": datetime.now(),
            }

            # Создаем турнирный матч (matchtype=2)
            await create_match(
                MODES["station5f"],  # Турниры всегда в Station 5 flags
                p1_data,
                p2_data,
                matchtype=2,
                tournament_id=tournament["id"],
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
        player_name = None
        # Находим ник игрока
        player_data = db_manager.fetchone(
            "players",
            "SELECT playername FROM players WHERE discordid = ?",
            (str(member.id),)
        )
        if player_data:
            player_name = player_data[0]
            if player_name in self.tournaments[tournament_name]["participants"]:
                # Обновляем список участников
                new_list = [p for p in self.tournaments[tournament_name]["participants"] if p != player_name]
                new_participants_str = ' '.join(new_list)
                
                db_manager.execute(
                    "tournaments",
                    "UPDATE tournaments SET currentplayers = ? WHERE id = ?",
                    (new_participants_str, self.tournaments[tournament_name]["id"])
                )
                self.tournaments[tournament_name]["participants"] = new_list

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

        # Получаем ник игрока
        player_name = None
        if self.is_user_verified(user.id):
            player_name = db_manager.fetchone(
                "players",
                "SELECT playername FROM players WHERE discordid = ?",
                (str(user.id),),
            )[0]

        if not player_name:
            return

        # Проверяем, зарегистрирован ли игрок
        if player_name not in tournament["participants"]:
            return

        # Обновляем список участников в базе
        new_list = [p for p in tournament["participants"] if p != player_name]
        new_participants_str = ' '.join(new_list)
        
        db_manager.execute(
            "tournaments",
            "UPDATE tournaments SET currentplayers = ? WHERE id = ?",
            (new_participants_str, tournament["id"])
        )

        # Обновляем в памяти
        tournament["participants"] = new_list
        await self.update_lists(tournament_name)

    async def update_lists(self, tournament_name):
        """Обновляет списки участников и забаненных"""
        tournament = self.tournaments[tournament_name]
        channels = tournament.get("channels", {})

        # Форматируем список участников
        participants = (
            "\n".join(
                f"{i+1}. {nick}"
                for i, nick in enumerate(tournament["participants"])
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
            # Участники турнира
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

            # Забаненные игроки
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

            # Черный список
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
        player_name = None
        if self.is_user_verified(user.id):
            player_name = db_manager.fetchone(
                "players",
                "SELECT playername FROM players WHERE discordid = ?",
                (str(user.id),),
            )[0]

        checks = {
            "not_in_db": not player_name,
            "blacklisted": await self.check_blacklist(user.id),
            "banned": user.id in tournament["banned"],
            "registered": player_name in tournament["participants"] if player_name else False,
            "globally_banned": self.is_user_globally_banned(user.id),
        }

        if any(checks.values()):
            return  # Регистрация отклонена

        # Регистрация
        try:
            # Обновляем список участников в базе данных
            new_participants = tournament["participants"] + [player_name]
            new_participants_str = ' '.join(new_participants)
            
            db_manager.execute(
                "tournaments",
                "UPDATE tournaments SET currentplayers = ? WHERE id = ?",
                (new_participants_str, tournament["id"])
            )

            # Обновляем в памяти
            tournament["participants"] = new_participants

            await self.update_lists(tournament_name)

            # Проверка набора участников
            if len(tournament["participants"]) >= tournament["slots"]:
                await tournament["channels"]["register"].send(
                    "🎉 Набрано достаточное количество участников! "
                    "Модератор может начать турнир командой `.tstart`"
                )

        except Exception as e:
            print(f"Ошибка при регистрации: {e}")

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

    async def is_active_tournament_match(self, match_id):
        """Проверяет, принадлежит ли матч активному турниру"""
        match_data = db_manager.fetchone(
            "matches",
            """SELECT tournament_id FROM matches 
            WHERE matchid = ? AND matchtype = 2 AND isover = 0""",
            (match_id,),
        )
        if not match_data:
            return False

        tournament_id = match_data[0]
        tournament = db_manager.fetchone(
            "tournaments",
            "SELECT started FROM tournaments WHERE id = ?",
            (tournament_id,),
        )

        return tournament and tournament[0] == 1  # Турнир должен быть начат


async def setup(bot):
    await bot.add_cog(Tournaments(bot))
