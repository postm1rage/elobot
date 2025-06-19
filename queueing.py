import discord
from discord.ui import View, Button, Select
from config import db, matches_db, MODES, MODE_NAMES, VERIFIED_ROLE_NAME
import asyncio
import sqlite3
from datetime import datetime
import random

# Очереди для каждого режима
queues = {mode: [] for mode in MODES.values()}


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


async def find_match(bot):
    while True:
        await asyncio.sleep(5)
        # Обработка стандартных режимов (1, 2, 3)
        for mode in [1, 2, 3]:
            queue = queues[mode]
            if len(queue) >= 2:
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

                if candidate:
                    player2 = queue.pop(candidate_idx)
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
                        INSERT INTO matches (mode, player1, player2)
                        VALUES (?, ?, ?)
                        """,
                        (mode, player1["nickname"], player2["nickname"]),
                    )
                    matches_db.commit()
                    match_id = c.lastrowid

                    # Уведомляем игроков
                    channel = bot.get_channel(player1["channel_id"])
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
                        user1 = await bot.fetch_user(player1["discord_id"])
                        user2 = await bot.fetch_user(player2["discord_id"])
                        await user1.send(
                            f"Ваш матч #{match_id} начинается! Режим: {mode_name}"
                        )
                        await user2.send(
                            f"Ваш матч #{match_id} начинается! Режим: {mode_name}"
                        )
                    except:
                        pass

        # Обработка режима "Any" (0)
        queue_any = queues[0]
        if queue_any:
            # Поиск в других режимах (1, 2, 3)
            min_diff = float("inf")
            candidate = None
            candidate_mode = None
            candidate_idx = None

            for mode in [1, 2, 3]:
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

                # Обновляем базу данных
                c = db.cursor()
                c.execute(
                    "UPDATE players SET in_queue = 0 WHERE playername IN (?, ?)",
                    (player_any["nickname"], candidate["nickname"]),
                )
                db.commit()

                # Создаем матч в режиме кандидата
                c = matches_db.cursor()
                c.execute(
                    """
                    INSERT INTO matches (mode, player1, player2)
                    VALUES (?, ?, ?)
                    """,
                    (candidate_mode, player_any["nickname"], candidate["nickname"]),
                )
                matches_db.commit()
                match_id = c.lastrowid

                # Уведомление
                channel = bot.get_channel(player_any["channel_id"])
                mode_name = MODE_NAMES.get(candidate_mode, "Unknown")

                embed = discord.Embed(
                    title="🎮 Матч найден!",
                    description=(
                        f"**Режим:** {mode_name}\n"
                        f"**Match ID:** {match_id}\n"
                        f"**Игрок 1:** {player_any['nickname']}\n"
                        f"**Игрок 2:** {candidate['nickname']}"
                    ),
                    color=discord.Color.green(),
                )
                await channel.send(embed=embed)

                try:
                    user1 = await bot.fetch_user(player_any["discord_id"])
                    user2 = await bot.fetch_user(candidate["discord_id"])
                    await user1.send(
                        f"Ваш матч #{match_id} начинается! Режим: {mode_name}"
                    )
                    await user2.send(
                        f"Ваш матч #{match_id} начинается! Режим: {mode_name}"
                    )
                except:
                    pass

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

                    if candidate:
                        player2 = queue_any.pop(candidate_idx)

                        # Обновляем базу
                        c = db.cursor()
                        c.execute(
                            "UPDATE players SET in_queue = 0 WHERE playername IN (?, ?)",
                            (player1["nickname"], player2["nickname"]),
                        )
                        db.commit()

                        # Случайный режим
                        random_mode = random.choice([1, 2, 3])
                        mode_name = MODE_NAMES.get(random_mode, "Unknown")

                        # Создаем матч
                        c = matches_db.cursor()
                        c.execute(
                            """
                            INSERT INTO matches (mode, player1, player2)
                            VALUES (?, ?, ?)
                            """,
                            (random_mode, player1["nickname"], player2["nickname"]),
                        )
                        matches_db.commit()
                        match_id = c.lastrowid

                        # Уведомление
                        channel = bot.get_channel(player1["channel_id"])
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

                        try:
                            user1 = await bot.fetch_user(player1["discord_id"])
                            user2 = await bot.fetch_user(player2["discord_id"])
                            await user1.send(
                                f"Ваш матч #{match_id} начинается! Режим: {mode_name}"
                            )
                            await user2.send(
                                f"Ваш матч #{match_id} начинается! Режим: {mode_name}"
                            )
                        except:
                            pass


def setup(bot):
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

        # Проверка активного матча
        c = db.cursor()
        c.execute(
            "SELECT in_queue FROM players WHERE discordid = ?", (str(ctx.author.id),)
        )
        player_data = c.fetchone()

        if not player_data:
            await ctx.send("❌ Вы не зарегистрированы в системе")
            return

        if player_data[0] == 1:
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
        c.execute(
            "SELECT playername FROM players WHERE discordid = ?", (str(ctx.author.id),)
        )
        nickname = c.fetchone()[0]
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
            "SELECT mode, player1, player2, player1score, player2score FROM matches WHERE matchid = ?",
            (self.match_id,),
        )
        match = c.fetchone()

        if not match:
            await interaction.response.send_message("Матч не найден", ephemeral=True)
            return

        mode, player1, player2, score1, score2 = match
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
