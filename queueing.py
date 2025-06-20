import discord
from discord.ui import View, Button, Select
from config import db, matches_db, MODES, MODE_NAMES, VERIFIED_ROLE_NAME, MAPS
import asyncio
import sqlite3
from datetime import datetime
from datetime import timedelta

import random


global_bot = None
# Очереди для каждого режима
queues = {mode: [] for mode in MODES.values()}

# Глобальные переменные для отслеживания процесса черкания
map_voting = (
    {}
)  # {match_id: {"players": [p1, p2], "remaining_maps": [...], "current_player": discord_id}}


def get_discord_id_by_nickname(nickname):
    c = db.cursor()
    c.execute(
        "SELECT discordid FROM players WHERE playername = ?",
        (nickname,),
    )
    result = c.fetchone()
    return int(result[0]) if result else None


# Объявляем функцию send_map_selection перед использованием
async def send_map_selection(match_id):
    global global_bot, map_voting
    if match_id not in map_voting:
        return

    voting = map_voting[match_id]
    current_player = voting["current_player"]
    remaining_maps = voting["remaining_maps"]

    try:
        player1 = await global_bot.fetch_user(voting["players"][0])
        player2 = await global_bot.fetch_user(voting["players"][1])
    except:
        return

    # Создаем View для выбора карты
    view = MapSelectionView(match_id, remaining_maps, current_player)

    # Отправляем сообщение текущему игроку
    try:
        msg = await global_bot.get_user(current_player).send(
            f"**Ваш ход!** Выберите карту для вычеркивания:", view=view
        )
        voting["messages"][current_player] = msg
    except:
        pass

    # Уведомляем другого игрока
    other_player = (
        voting["players"][1]
        if current_player == voting["players"][0]
        else voting["players"][0]
    )
    try:
        await global_bot.get_user(other_player).send(
            f"Ожидайте своего хода. Сейчас выбирает {global_bot.get_user(current_player).mention}."
        )
    except:
        pass


class MapSelectionView(View):
    def __init__(self, match_id, maps, player_id):
        super().__init__(timeout=120)
        self.match_id = match_id
        self.player_id = player_id

        for map_name in maps:
            button = Button(
                label=map_name,
                style=discord.ButtonStyle.secondary,
                custom_id=f"map_{map_name}",
            )
            button.callback = lambda i, b=button: self.button_callback(i, b)
            self.add_item(button)

    async def button_callback(self, interaction: discord.Interaction, button: Button):
        global map_voting
        if self.match_id not in map_voting:
            await interaction.response.send_message(
                "Процесс выбора карты завершен", ephemeral=True
            )
            return

        voting = map_voting[self.match_id]
        selected_map = button.label

        # Удаляем выбранную карту
        if selected_map in voting["remaining_maps"]:
            voting["remaining_maps"].remove(selected_map)

        voting["last_selected"] = selected_map

        # Если осталась только одна карта - завершаем процесс
        if len(voting["remaining_maps"]) == 1:  # Изменено с <= 1 на == 1
            final_map = voting["remaining_maps"][0]
            print(f"Черкание завершено! Карта: {final_map}")

            await interaction.response.edit_message(
                content=f"Вы вычеркнули карту **{selected_map}**", view=None
            )
            await self.finish_map_selection()
            return
        # Если карт не осталось вообще (маловероятно, но на всякий случай)
        elif not voting["remaining_maps"]:
            final_map = voting.get("last_selected", "Станция")
            print(f"Черкание завершено! Карта: {final_map} (последняя вычеркнутая)")

            await interaction.response.edit_message(
                content=f"Вы вычеркнули карту **{selected_map}**", view=None
            )
            await self.finish_map_selection()
            return

        # Переключаем ход
        voting["current_player"] = (
            voting["players"][1]
            if voting["current_player"] == voting["players"][0]
            else voting["players"][0]
        )

        await interaction.response.edit_message(
            content=f"Вы вычеркнули карту **{selected_map}**", view=None
        )
        await send_map_selection(self.match_id)

    async def auto_select_map(self):
        global map_voting
        if self.match_id not in map_voting:
            return

        voting = map_voting[self.match_id]

        # Если осталась только одна карта - завершаем
        if len(voting["remaining_maps"]) == 1:
            return

        # Выбираем случайную карту
        selected_map = random.choice(voting["remaining_maps"])
        voting["remaining_maps"].remove(selected_map)
        voting["last_selected"] = selected_map

        # Если после удаления осталась одна карта
        if len(voting["remaining_maps"]) == 1:
            try:
                await voting["messages"][self.player_id].edit(
                    content=f"⏱ Вы не успели выбрать! Автоматически вычеркнута карта **{selected_map}**",
                    view=None,
                )
            except:
                pass
            await self.finish_map_selection()
            return

        # Переключаем ход
        voting["current_player"] = (
            voting["players"][1]
            if voting["current_player"] == voting["players"][0]
            else voting["players"][0]
        )

        try:
            await voting["messages"][self.player_id].edit(
                content=f"⏱ Вы не успели выбрать! Автоматически вычеркнута карта **{selected_map}**",
                view=None,
            )
        except:
            pass

        await send_map_selection(self.match_id)

    async def finish_map_selection(self):
        global map_voting
        if self.match_id not in map_voting:
            print(f"ОШИБКА: Данные матча {self.match_id} потеряны!")
            return

        voting = map_voting.get(self.match_id)
        if not voting or not voting.get("remaining_maps"):
            print(f"ОШИБКА: Нет данных о картах для матча {self.match_id}")
            return
        selected_map = (
            voting["remaining_maps"][0] if voting["remaining_maps"] else "Станция"
        )

        # Сохраняем выбранную карту в базе данных
        c = matches_db.cursor()
        c.execute(
            "UPDATE matches SET map = ? WHERE matchid = ?",
            (selected_map, self.match_id),
        )
        matches_db.commit()

        # Отправляем результат обоим игрокам
        for player_id in voting["players"]:
            # Определяем соперника
            opponent_id = next(pid for pid in voting["players"] if pid != player_id)

            # Безопасное получение никнейма
            if (
                "player_nicknames" in voting
                and opponent_id in voting["player_nicknames"]
            ):
                opponent_nickname = voting["player_nicknames"][opponent_id]
            else:
                # Если нет в словаре, попробуем получить из БД
                try:
                    c_db = db.cursor()
                    # Исправленная строка: правильный синтаксис параметра
                    c_db.execute(
                        "SELECT playername FROM players WHERE discordid = ?",
                        (str(opponent_id),),  # <-- Запятая внутри кортежа
                    )
                    player_data = c_db.fetchone()
                    opponent_nickname = (
                        player_data[0] if player_data else "Неизвестный игрок"
                    )
                except Exception as e:
                    print(f"Ошибка при получении никнейма из БД: {e}")
                    opponent_nickname = "Неизвестный игрок"

            try:
                # Получаем информацию о сопернике
                opponent_user = await global_bot.fetch_user(opponent_id)
                discord_tag = f"{opponent_user.name}#{opponent_user.discriminator}"
            except:
                discord_tag = "неизвестен"

            # Создаем embed
            embed = discord.Embed(
                title="Черкание завершено", color=discord.Color.green()
            )
            embed.add_field(name="Карта", value=f"**{selected_map}**", inline=False)
            embed.add_field(
                name="Противник", value=f"**{opponent_nickname}**", inline=False
            )
            embed.add_field(name="Discord противника", value=discord_tag, inline=False)
            embed.set_footer(text=f"Match ID: {self.match_id}")

            try:
                await global_bot.get_user(player_id).send(embed=embed)
            except Exception as e:
                print(f"Ошибка при отправке сообщения игроку {player_id}: {e}")

        # Удаляем данные о голосовании
        if self.match_id in map_voting:
            del map_voting[self.match_id]


class ModeSelectView(View):
    def __init__(self, player_id):
        super().__init__(timeout=30)
        self.player_id = player_id
        self.selected_mode = None

        options = [
            discord.SelectOption(label="Any", value="0"),
            discord.SelectOption(label="Station 5 flags", value="1"),
            discord.SelectOption(label="MotS Solo", value="2"),
            discord.SelectOption(label="12min", value="3"),
        ]

        self.select = Select(placeholder="Выберите режим игры", options=options)
        self.select.callback = self.select_callback
        self.add_item(self.select)

    async def select_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.player_id:
            await interaction.response.send_message(
                "Это не ваша очередь!", ephemeral=True
            )
            return

        self.selected_mode = int(self.select.values[0])
        await interaction.response.defer()
        self.stop()


def calculate_elo(player1_rating, player2_rating, result, K=40, C=400, max_rating=4000):
    expected_1 = 1 / (1 + 10 ** ((player2_rating - player1_rating) / C))
    expected_2 = 1 - expected_1

    weight_1 = max_rating / (max_rating + player1_rating)
    weight_2 = max_rating / (max_rating + player2_rating)

    new_rating_1 = player1_rating + K * (result - expected_1) * weight_1
    new_rating_2 = player2_rating + K * ((1 - result) - expected_2) * weight_2

    return round(new_rating_1), round(new_rating_2)


def get_player_rating(nickname, mode):
    c = db.cursor()
    if mode == MODES["station5f"]:
        c.execute("SELECT elo_station5f FROM players WHERE playername = ?", (nickname,))
    elif mode == MODES["mots"]:
        c.execute("SELECT elo_mots FROM players WHERE playername = ?", (nickname,))
    elif mode == MODES["12min"]:
        c.execute("SELECT elo_12min FROM players WHERE playername = ?", (nickname,))
    else:
        c.execute("SELECT currentelo FROM players WHERE playername = ?", (nickname,))

    rating = c.fetchone()
    return rating[0] if rating else 1000


def update_player_rating(nickname, new_rating, mode):
    c = db.cursor()

    # Обновляем ELO для конкретного режима
    if mode == MODES["station5f"]:
        c.execute(
            "UPDATE players SET elo_station5f = ? WHERE playername = ?",
            (new_rating, nickname),
        )
    elif mode == MODES["mots"]:
        c.execute(
            "UPDATE players SET elo_mots = ? WHERE playername = ?",
            (new_rating, nickname),
        )
    elif mode == MODES["12min"]:
        c.execute(
            "UPDATE players SET elo_12min = ? WHERE playername = ?",
            (new_rating, nickname),
        )

    # Обновляем суммарный ELO
    c.execute(
        """
    UPDATE players 
    SET currentelo = elo_station5f + elo_mots + elo_12min 
    WHERE playername = ?
    """,
        (nickname,),
    )

    db.commit()


async def find_match():
    global global_bot
    while True:
        await asyncio.sleep(5)
        print(f"Checking queues: {[len(q) for q in queues.values()]}")

        # Обработка стандартных режимов (1, 2, 3)
        for mode in [MODES["station5f"], MODES["mots"], MODES["12min"]]:
            queue = queues[mode]
            if len(queue) >= 2:
                try:
                    queue.sort(key=lambda x: x["join_time"])
                    player1 = queue.pop(0)
                    min_diff = float("inf")
                    candidate = None
                    candidate_idx = None

                    for idx, p in enumerate(queue):
                        diff = abs(player1["rating"] - p["rating"])
                        if diff < min_diff:
                            min_diff = diff
                            candidate = p
                            candidate_idx = idx

                    if candidate_idx is not None:
                        player2 = queue.pop(candidate_idx)
                        await create_match(mode, player1, player2)
                except Exception as e:
                    print(f"Error processing {MODE_NAMES[mode]} queue: {e}")
                    continue

        # Обработка режима "Any" (0)
        queue_any = queues[MODES["any"]]
        if queue_any:
            try:
                # Поиск в других режимах (1, 2, 3)
                min_diff = float("inf")
                candidate = None
                candidate_mode = None
                candidate_idx = None

                for mode in [MODES["station5f"], MODES["mots"], MODES["12min"]]:
                    queue = queues[mode]
                    for idx, p in enumerate(queue):
                        diff = abs(queue_any[0]["rating"] - p["rating"])
                        if diff < min_diff:
                            min_diff = diff
                            candidate = p
                            candidate_mode = mode
                            candidate_idx = idx

                if candidate:
                    player_any = queue_any.pop(0)
                    queues[candidate_mode].pop(candidate_idx)
                    await create_match(candidate_mode, player_any, candidate)
                else:
                    # Поиск внутри очереди "Any"
                    if len(queue_any) >= 2:
                        player1 = queue_any.pop(0)
                        min_diff = float("inf")
                        candidate = None
                        candidate_idx = None

                        for idx, p in enumerate(queue_any):
                            diff = abs(player1["rating"] - p["rating"])
                            if diff < min_diff:
                                min_diff = diff
                                candidate = p
                                candidate_idx = idx

                        if candidate_idx is not None:
                            player2 = queue_any.pop(candidate_idx)
                            random_mode = random.choice([MODES["station5f"], MODES["mots"], MODES["12min"]])
                            await create_match(random_mode, player1, player2)
            except Exception as e:
                print(f"Error processing Any queue: {e}")
                continue

async def create_match(mode, player1, player2):
    """Создает матч и уведомляет игроков"""
    try:
        # Обновляем статус в базе
        c = db.cursor()
        c.execute(
            "UPDATE players SET in_queue = 0 WHERE playername IN (?, ?)",
            (player1["nickname"], player2["nickname"]),
        )
        db.commit()

        # Создаем запись о матче
        c = matches_db.cursor()
        c.execute(
            """
            INSERT INTO matches (mode, player1, player2, start_time)
            VALUES (?, ?, ?, ?)
            """,
            (mode, player1["nickname"], player2["nickname"], datetime.now()),
        )
        matches_db.commit()
        match_id = c.lastrowid

        # Уведомляем игроков
        channel = global_bot.get_channel(player1["channel_id"])
        mode_name = MODE_NAMES.get(mode, "Unknown")

        embed = discord.Embed(
            title="🎮 Матч найден!",
            description=(
                f"**Режим:** {mode_name}\n"
                f"**Match ID:** {match_id}\n"
                f"**Игрок 1:** {player1['nickname']}\n"
                f"**Игрок 2:** {player2['nickname']}"
            ),
            color=discord.Color.green(),
        )
        await channel.send(embed=embed)

        # Личные сообщения
        try:
            user1 = await global_bot.fetch_user(player1["discord_id"])
            user2 = await global_bot.fetch_user(player2["discord_id"])
            await user1.send(f"Ваш матч #{match_id} начинается! Режим: {mode_name}")
            await user2.send(f"Ваш матч #{match_id} начинается! Режим: {mode_name}")
        except Exception as e:
            print(f"Error sending DM: {e}")

        # Для MotS и 12min инициируем черкание карт
        if mode in [MODES["mots"], MODES["12min"]]:
            map_voting[match_id] = {
                "players": [player1["discord_id"], player2["discord_id"]],
                "player_nicknames": {
                    player1["discord_id"]: player1["nickname"],
                    player2["discord_id"]: player2["nickname"],
                },
                "remaining_maps": MAPS.copy(),
                "current_player": player1["discord_id"],
                "messages": {},
            }
            await send_map_selection(match_id)
    except Exception as e:
        print(f"Error creating match: {e}")

def setup(bot):
    global global_bot
    global_bot = bot

    @bot.command()
    async def play(ctx):
        # Проверка канала
        if ctx.channel.name != "elobot-queue":
            return

        # Проверка верификации
        verified_role = discord.utils.get(ctx.guild.roles, name=VERIFIED_ROLE_NAME)
        if not verified_role or verified_role not in ctx.author.roles:
            await ctx.send("❌ Требуется верификация для поиска игры")
            return

        # Получаем информацию об игроке
        c = db.cursor()
        c.execute(
            "SELECT playername, in_queue FROM players WHERE discordid = ?",
            (str(ctx.author.id),),
        )
        player_data = c.fetchone()

        if not player_data:
            await ctx.send("❌ Вы не зарегистрированы в системе")
            return

        nickname, in_queue = player_data

        # +++ ПРОВЕРКА АКТИВНЫХ МАТЧЕЙ +++
        c_matches = matches_db.cursor()
        c_matches.execute(
            """
            SELECT matchid 
            FROM matches 
            WHERE (player1 = ? OR player2 = ?) 
            AND isover = 0
            """,
            (nickname, nickname),
        )
        active_match = c_matches.fetchone()

        if active_match:
            await ctx.send(
                f"❌ У вас есть активный матч (ID: {active_match[0]}). "
                "Завершите его или сдайтесь командой !giveup перед поиском новой игры."
            )
            return
        # --- КОНЕЦ ПРОВЕРКИ АКТИВНЫХ МАТЧЕЙ ---

        if in_queue == 1:
            await ctx.send("❌ Вы уже в очереди")
            return

        # Выбор режима
        view = ModeSelectView(ctx.author.id)
        msg = await ctx.send("Выберите режим игры:", view=view)

        await view.wait()

        if view.selected_mode is None:  # Таймаут или отмена
            await msg.edit(content="⌛ Время выбора истекло", view=None)
            return

        # Добавление в очередь
        rating = get_player_rating(nickname, view.selected_mode)

        queues[view.selected_mode].append(
            {
                "discord_id": ctx.author.id,
                "nickname": nickname,
                "rating": rating,
                "channel_id": ctx.channel.id,
                "join_time": datetime.now(),
            }
        )

        c.execute(
            "UPDATE players SET in_queue = 1 WHERE discordid = ?", (str(ctx.author.id),)
        )
        db.commit()

        await msg.edit(
            content=f"🔍 Поиск игры в режиме {MODE_NAMES[view.selected_mode]}...",
            view=None,
        )

    @bot.command()
    async def leave(ctx):
        if ctx.channel.name != "elobot-queue":
            return

        c = db.cursor()
        c.execute(
            "SELECT playername, in_queue FROM players WHERE discordid = ?",
            (str(ctx.author.id),),
        )
        player_data = c.fetchone()

        if not player_data or player_data[1] == 0:
            await ctx.send("❌ Вы не в очереди")
            return

        # Удаление из всех очередей
        for mode, queue in queues.items():
            queues[mode] = [p for p in queue if p["discord_id"] != ctx.author.id]

        c.execute(
            "UPDATE players SET in_queue = 0 WHERE discordid = ?", (str(ctx.author.id),)
        )
        db.commit()
        await ctx.send("✅ Вы вышли из очереди")

    @bot.command()
    async def queue(ctx):
        # Проверка канала
        if ctx.channel.name != "elobot-queue":
            return

        # Создаем Embed
        embed = discord.Embed(
            title="📊 Статистика очередей", color=discord.Color.blue()
        )

        # Собираем статистику по режимам
        display_order = [
            (MODES["mots"], "MotS Solo", "🔫"),
            (MODES["12min"], "12 Minute", "⏱️"),
            (MODES["station5f"], "Station 5 Flags", "🚩"),
            (MODES["any"], "Any Mode", "🎲"),
        ]

        # Получаем количество игроков в каждой очереди
        for mode_id, mode_name, emoji in display_order:
            count = len(queues[mode_id])
            embed.add_field(
                name=f"{emoji} {mode_name}",
                value=f"`{count}` игроков в очереди",
                inline=True,
            )

        # Получаем общее количество игроков в активных матчах
        c = db.cursor()
        c.execute("SELECT COUNT(*) FROM players WHERE in_queue = 1")
        total_in_queue = c.fetchone()[0] or 0

        # Получаем количество игроков в активных матчах
        c = matches_db.cursor()
        c.execute(
            """
            SELECT COUNT(DISTINCT player) 
            FROM (
                SELECT player1 AS player FROM matches WHERE isover = 0
                UNION ALL
                SELECT player2 AS player FROM matches WHERE isover = 0
            )
        """
        )
        total_in_matches = c.fetchone()[0] or 0

        # Общее количество игроков "в игре"
        total_in_game = total_in_queue + total_in_matches

        # Добавляем общую информацию
        embed.description = (
            f"**Всего игроков в игре:** `{total_in_game}`\n"
            f"• В очередях: `{total_in_queue}`\n"
            f"• В активных матчах: `{total_in_matches}`"
        )

        # Добавляем время последнего обновления
        embed.set_footer(
            text=f"Последнее обновление: {datetime.now().strftime('%H:%M:%S')}"
        )

        await ctx.send(embed=embed)

    @bot.command()
    async def giveup(ctx):
        # Проверяем, что команда вызвана в нужном канале или в ЛС боту
        if not (
            ctx.channel.name == "elobot-queue"
            or isinstance(ctx.channel, discord.DMChannel)
        ):
            return

        # Проверяем верификацию
        if isinstance(ctx.channel, discord.TextChannel):
            verified_role = discord.utils.get(ctx.guild.roles, name=VERIFIED_ROLE_NAME)
            if not verified_role or verified_role not in ctx.author.roles:
                await ctx.send(
                    "❌ Требуется верификация для использования этой команды"
                )
                return

        # Находим активный матч игрока
        c = db.cursor()
        c.execute(
            "SELECT playername FROM players WHERE discordid = ?", (str(ctx.author.id),)
        )
        player_data = c.fetchone()

        if not player_data:
            await ctx.send("❌ Вы не зарегистрированы в системе")
            return

        nickname = player_data[0]

        c = matches_db.cursor()
        c.execute(
            """
            SELECT matchid, mode, player1, player2 
            FROM matches 
            WHERE (player1 = ? OR player2 = ?) 
            AND isover = 0
            """,
            (nickname, nickname),
        )
        match_data = c.fetchone()

        if not match_data:
            await ctx.send("❌ У вас нет активных матчей")
            return

        match_id, mode, player1, player2 = match_data

        # Определяем победителя и проигравшего
        if nickname == player1:
            winner = player2
            loser = player1
            player1_score = 0
            player2_score = 1
        else:
            winner = player1
            loser = player2
            player1_score = 1
            player2_score = 0

        # Обновляем запись матча
        c.execute(
            """
            UPDATE matches 
            SET player1score = ?, player2score = ?, isover = 1, isverified = 1
            WHERE matchid = ?
            """,
            (player1_score, player2_score, match_id),
        )
        matches_db.commit()

        # Обновляем статистику игроков
        c = db.cursor()
        c.execute(
            "UPDATE players SET wins = wins + 1 WHERE playername = ?",
            (winner,),
        )
        c.execute(
            "UPDATE players SET losses = losses + 1 WHERE playername = ?",
            (loser,),
        )

        # Обновляем ELO
        winner_rating = get_player_rating(winner, mode)
        loser_rating = get_player_rating(loser, mode)
        new_winner_rating, new_loser_rating = calculate_elo(
            winner_rating, loser_rating, 1 if winner == player1 else 0
        )

        update_player_rating(winner, new_winner_rating, mode)
        update_player_rating(loser, new_loser_rating, mode)

        # Отправляем уведомление
        mode_name = MODE_NAMES.get(mode, "Unknown")
        embed = discord.Embed(
            title="🏳️ Матч завершен (сдача)",
            description=(
                f"**Match ID:** {match_id}\n"
                f"**Режим:** {mode_name}\n"
                f"**Победитель:** {winner}\n"
                f"**Проигравший:** {loser}\n\n"
                f"**Изменения ELO:**\n"
                f"{winner}: {winner_rating} → **{new_winner_rating}** (+{new_winner_rating - winner_rating})\n"
                f"{loser}: {loser_rating} → **{new_loser_rating}** ({new_loser_rating - loser_rating})"
            ),
            color=discord.Color.red(),
        )

        embed_channel = discord.Embed(  ## embed для отправки в канал результатов
            title="🏳️ Матч завершен (сдача)",
            description=(
                f"**Match ID:** {match_id}\n"
                f"**Режим:** {mode_name}\n"
                f"**Победитель:** {winner}\n"
                f"**Проигравший:** {loser}\n\n"
                f"**Изменения ELO:**\n"
                f"{winner}: {winner_rating} → **{new_winner_rating}** (+{new_winner_rating - winner_rating})\n"
                f"{loser}: {loser_rating} → **{new_loser_rating}** ({new_loser_rating - loser_rating})"
            ),
            color=discord.Color.red(),
        )

        # +++ ДОБАВЛЯЕМ ОТПРАВКУ В КАНАЛ РЕЗУЛЬТАТОВ +++
        # Ищем канал elobot-results
        results_channel_found = None
        for guild in global_bot.guilds:
            results_channel = discord.utils.get(
                guild.text_channels, name="elobot-results"
            )
            if results_channel:
                results_channel_found = results_channel
                break

        if results_channel_found:
            try:
                await results_channel_found.send(embed=embed_channel)
            except Exception as e:
                print(f"Ошибка при отправке в канал результатов: {e}")
                # Пытаемся отправить в канал очереди как запасной вариант
                try:
                    if isinstance(ctx.channel, discord.TextChannel):
                        await ctx.send(
                            f"⚠ Не удалось отправить в канал результатов: {e}"
                        )
                except:
                    pass
        else:
            print("Канал elobot-results не найден ни на одном сервере")
            try:
                if isinstance(ctx.channel, discord.TextChannel):
                    await ctx.send("⚠ Канал elobot-results не найден")
            except:
                pass

        if isinstance(ctx.channel, discord.TextChannel):
            await ctx.send("✅ Вы сдались. Матч завершен.")
        else:
            await ctx.send(
                "✅ Вы сдались. Матч завершен. Результаты отправлены обоим игрокам."
            )

        try:
            # Отправляем в ЛС обоим игрокам
            winner_user = await global_bot.fetch_user(
                get_discord_id_by_nickname(winner)
            )
            loser_user = await global_bot.fetch_user(get_discord_id_by_nickname(loser))

            await winner_user.send(embed=embed)
            await loser_user.send(embed=embed)
        except Exception as e:
            print(f"Ошибка при отправке уведомления о сдаче: {e}")

        if isinstance(ctx.channel, discord.TextChannel):
            await ctx.send("✅ Вы сдались. Матч завершен.")
        else:
            await ctx.send(
                "✅ Вы сдались. Матч завершен. Результаты отправлены обоим игрокам."
            )

    @bot.event
    async def on_ready():
        bot.loop.create_task(check_expired_matches())

    async def check_expired_matches():
        await bot.wait_until_ready()
        while not bot.is_closed():
            await asyncio.sleep(300)  # Проверка каждые 5 минут

            now = datetime.now()
            one_hour_ago = now - timedelta(hours=1)

            c = matches_db.cursor()
            c.execute(
                "SELECT matchid, mode, player1, player2, start_time FROM matches WHERE isover = 0 AND start_time < ?",
                (one_hour_ago,),
            )
            expired_matches = c.fetchall()

            for match in expired_matches:
                match_id, mode, player1_name, player2_name, start_time = match

                # Двойная проверка статуса матча
                c_check = matches_db.cursor()
                c_check.execute(
                    "SELECT isover FROM matches WHERE matchid = ?", (match_id,)
                )
                if c_check.fetchone()[0] == 1:
                    continue

                # Обновляем матч как ничью
                c_update = matches_db.cursor()
                c_update.execute(
                    "UPDATE matches SET player1score = 0, player2score = 0, isover = 1, isverified = 1 WHERE matchid = ?",
                    (match_id,),
                )
                matches_db.commit()

                # Обновляем статистику игроков
                rating1 = get_player_rating(player1_name, mode)
                rating2 = get_player_rating(player2_name, mode)
                new_rating1, new_rating2 = calculate_elo(rating1, rating2, 0.5)  # Ничья

                update_player_rating(player1_name, new_rating1, mode)
                update_player_rating(player2_name, new_rating2, mode)

                # Обновляем счетчики ничьих
                c_db = db.cursor()
                if mode == MODES["station5f"]:
                    c_db.execute(
                        "UPDATE players SET ties_station5f = ties_station5f + 1 WHERE playername = ?",
                        (player1_name,),
                    )
                    c_db.execute(
                        "UPDATE players SET ties_station5f = ties_station5f + 1 WHERE playername = ?",
                        (player2_name,),
                    )
                elif mode == MODES["mots"]:
                    c_db.execute(
                        "UPDATE players SET ties_mots = ties_mots + 1 WHERE playername = ?",
                        (player1_name,),
                    )
                    c_db.execute(
                        "UPDATE players SET ties_mots = ties_mots + 1 WHERE playername = ?",
                        (player2_name,),
                    )
                elif mode == MODES["12min"]:
                    c_db.execute(
                        "UPDATE players SET ties_12min = ties_12min + 1 WHERE playername = ?",
                        (player1_name,),
                    )
                    c_db.execute(
                        "UPDATE players SET ties_12min = ties_12min + 1 WHERE playername = ?",
                        (player2_name,),
                    )

                c_db.execute(
                    "UPDATE players SET ties = ties + 1 WHERE playername IN (?, ?)",
                    (player1_name, player2_name),
                )
                db.commit()

                # Уведомление игроков в ЛС
                try:
                    user1_id = get_discord_id_by_nickname(player1_name)
                    user2_id = get_discord_id_by_nickname(player2_name)
                    user1 = await global_bot.fetch_user(user1_id)
                    user2 = await global_bot.fetch_user(user2_id)

                    embed_dm = discord.Embed(
                        title="⏱ Матч завершен автоматически",
                        description=(
                            f"Матч #{match_id} между **{player1_name}** и **{player2_name}**\n"
                            f"Режим: **{MODE_NAMES.get(mode, 'Unknown')}**\n"
                            f"Был автоматически завершен вничью, так как превышено время (1 час).\n\n"
                            f"**Изменения ELO:**\n"
                            f"{player1_name}: {rating1} → **{new_rating1}** ({new_rating1 - rating1:+})\n"
                            f"{player2_name}: {rating2} → **{new_rating2}** ({new_rating2 - rating2:+})"
                        ),
                        color=discord.Color.orange(),
                    )
                    await user1.send(embed=embed_dm)
                    await user2.send(embed=embed_dm)
                except Exception as e:
                    print(f"Ошибка при отправке уведомления: {e}")

                # +++ ОТПРАВКА В КАНАЛ РЕЗУЛЬТАТОВ +++
                embed_channel = discord.Embed(
                    title="⏱ Матч завершен (время вышло)",
                    description=(
                        f"**Match ID:** {match_id}\n"
                        f"**Режим:** {MODE_NAMES.get(mode, 'Unknown')}\n"
                        f"**Игроки:** {player1_name} vs {player2_name}\n"
                        f"**Результат:** Ничья 0:0\n\n"
                        f"**Причина:** Превышено максимальное время матча (1 час)\n\n"
                        f"**Изменения ELO:**\n"
                        f"{player1_name}: {rating1} → **{new_rating1}** ({new_rating1 - rating1:+})\n"
                        f"{player2_name}: {rating2} → **{new_rating2}** ({new_rating2 - rating2:+})"
                    ),
                    color=discord.Color.gold(),  # Желтый цвет
                )
                embed_channel.set_footer(
                    text="Матч завершен автоматически по истечении времени"
                )

                # Ищем канал elobot-results
                results_channel_found = None
                for guild in global_bot.guilds:
                    results_channel = discord.utils.get(
                        guild.text_channels, name="elobot-results"
                    )
                    if results_channel:
                        results_channel_found = results_channel
                        break

                if results_channel_found:
                    try:
                        await results_channel_found.send(embed=embed_channel)
                    except Exception as e:
                        print(f"Ошибка при отправке в канал результатов: {e}")


class ConfirmMatchView(View):
    def __init__(self, match_id, bot):  # Добавляем bot в параметры
        super().__init__(timeout=None)
        self.match_id = match_id
        self.bot = bot  # Сохраняем экземпляр бота

    @discord.ui.button(label="Подтвердить", style=discord.ButtonStyle.green)
    async def confirm_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        c = matches_db.cursor()
        c.execute(
            "SELECT mode, player1, player2, player1score, player2score, map FROM matches WHERE matchid = ?",
            (self.match_id,),
        )
        match = c.fetchone()

        if not match:
            await interaction.response.send_message("Матч не найден", ephemeral=True)
            return

        mode, player1, player2, score1, score2, map_name = match
        mode_name = MODE_NAMES.get(mode, "Unknown")

        # Определяем результат
        if score1 > score2:
            result = 1  # Победа player1
        elif score1 < score2:
            result = 0  # Победа player2
        else:
            result = 0.5  # Ничья

        # Получаем текущие рейтинги
        old_rating1 = get_player_rating(player1, mode)
        old_rating2 = get_player_rating(player2, mode)

        # Рассчитываем новые рейтинги
        new_rating1, new_rating2 = calculate_elo(old_rating1, old_rating2, result)

        # Обновляем рейтинги
        update_player_rating(player1, new_rating1, mode)
        update_player_rating(player2, new_rating2, mode)

        # Обновляем статистику
        c = db.cursor()
        if result == 1:
            c.execute(
                "UPDATE players SET wins = wins + 1 WHERE playername = ?", (player1,)
            )
            c.execute(
                "UPDATE players SET losses = losses + 1 WHERE playername = ?",
                (player2,),
            )
        elif result == 0:
            c.execute(
                "UPDATE players SET wins = wins + 1 WHERE playername = ?", (player2,)
            )
            c.execute(
                "UPDATE players SET losses = losses + 1 WHERE playername = ?",
                (player1,),
            )
        else:
            c.execute(
                "UPDATE players SET ties = ties + 1 WHERE playername IN (?, ?)",
                (player1, player2),
            )

        c.execute(
            "UPDATE players SET currentmatches = currentmatches + 1 WHERE playername IN (?, ?)",
            (player1, player2),
        )
        db.commit()

        # Помечаем матч как завершенный
        c = matches_db.cursor()
        c.execute(
            "UPDATE matches SET isover = 1, isverified = 1 WHERE matchid = ?",
            (self.match_id,),
        )
        matches_db.commit()

        # Рассчитываем изменения ELO
        elo_change1 = new_rating1 - old_rating1
        elo_change2 = new_rating2 - old_rating2

        embed = discord.Embed(
            title=f"✅ Матч подтверждён | ID: {self.match_id}",
            description=(
                f"**Режим:** {mode_name}\n"
                f"**Карта:** {map_name if map_name else 'не выбрана'}\n"  # Добавлено
                f"**Игроки:** {player1} vs {player2}\n"
                f"**Счёт:** {score1} - {score2}\n\n"
                f"**Изменения ELO ({mode_name}):**\n"
                f"{player1}: {old_rating1} → **{new_rating1}** ({'+' if elo_change1 >= 0 else ''}{elo_change1})\n"
                f"{player2}: {old_rating2} → **{new_rating2}** ({'+' if elo_change2 >= 0 else ''}{elo_change2})"
            ),
            color=discord.Color.green(),
        )

        # Отправляем отчёт в канал результатов
        for guild in self.bot.guilds:
            results_channel = discord.utils.get(
                guild.text_channels, name="elobot-results"
            )
            if results_channel:
                embed = discord.Embed(
                    title=f"✅ Матч подтверждён | ID: {self.match_id}",
                    description=(
                        f"**Режим:** {mode_name}\n"
                        f"**Игроки:** {player1} vs {player2}\n"
                        f"**Счёт:** {score1} - {score2}\n\n"
                        f"**Изменения ELO ({mode_name}):**\n"
                        f"{player1}: {old_rating1} → **{new_rating1}** ({'+' if elo_change1 >= 0 else ''}{elo_change1})\n"
                        f"{player2}: {old_rating2} → **{new_rating2}** ({'+' if elo_change2 >= 0 else ''}{elo_change2})"
                    ),
                    color=discord.Color.green(),
                )
                await results_channel.send(embed=embed)
                break  # Отправляем только в первый найденный канал

        await interaction.response.send_message("Матч подтвержден!", ephemeral=True)
        await interaction.message.edit(view=None)

    @discord.ui.button(label="Отклонить", style=discord.ButtonStyle.red)
    async def reject_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        c = matches_db.cursor()
        c.execute(
            "UPDATE matches SET isverified = 2 WHERE matchid = ?", (self.match_id,)
        )
        matches_db.commit()

        # Логирование отклонения
        guild = interaction.guild
        if guild:
            results_channel = discord.utils.get(guild.text_channels, name="elobot-logs")
            if results_channel:
                await results_channel.send(
                    f"❌ Подтверждение матча {self.match_id} отклонено"
                )

        await interaction.response.send_message("Матч отклонен", ephemeral=True)
        await interaction.message.edit(view=None)
