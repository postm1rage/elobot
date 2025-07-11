import logging
from dotenv import load_dotenv
import os
from datetime import datetime
from role_manager import setup_role_manager
from nickname_updater import setup_nickname_updater
from tournaments import setup as setup_tournaments
import tournament_test
from config import (
    bot,
    MODERATOR_ID,
    MODE_NAMES,
    VERIFY_CHANNEL_NAME,
    RESULTS_CHANNEL_NAME,
    LEADERBOARD_MODES,
    MODES,
)
from verification import (
    setup_verified_role,
    setup as setup_verification,
    VerifyView,
)
from ban import setup as setup_ban
from queueing import setup as setup_queueing, ConfirmMatchView, find_match
from queueing import check_expired_matches
import re
from discord.ui import View, Button, Select
import discord
from db_manager import db_manager

load_dotenv()
token = os.getenv("DISCORD_TOKEN")

handler = logging.FileHandler(filename="discord.log", encoding="utf-8", mode="w")


class LeaderboardView(discord.ui.View):
    """View с кнопками для переключения режимов лидерборда"""

    def __init__(self, current_mode):
        super().__init__(timeout=180)
        self.current_mode = current_mode

        # Создаем кнопки для всех режимов
        modes = [
            ("🌟 Общий", "overall", discord.ButtonStyle.green),
            ("🚩 Station", "station5flags", discord.ButtonStyle.blurple),
            ("🔫 MotS", "mots", discord.ButtonStyle.red),
            ("⏱ 12min", "12min", discord.ButtonStyle.grey),
        ]

        for label, mode, style in modes:
            # Для текущего режима делаем кнопку неактивной
            disabled = mode == current_mode
            button = discord.ui.Button(
                label=label, style=style, custom_id=f"lb_{mode}", disabled=disabled
            )
            button.callback = lambda i, m=mode: self.button_callback(i, m)
            self.add_item(button)

    async def button_callback(self, interaction: discord.Interaction, mode: str):
        """Обработчик нажатия кнопки"""
        # Обновляем лидерборд для выбранного режима
        await send_leaderboard(interaction, mode)
        await interaction.response.defer()

    async def on_timeout(self):
        """Делаем все кнопки неактивными после таймаута"""
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        try:
            await self.message.edit(view=self)
        except:
            pass


@bot.event
async def on_ready():
    print(f"Бот {bot.user.name} запущен!")

    # Сбрасываем флаги в очереди в БД
    try:
        db_manager.execute("players", "UPDATE players SET in_queue = 0")
        print("[INIT] Сброшены флаги in_queue для всех игроков")
    except Exception as e:
        print(f"[INIT] Ошибка сброса флагов: {e}")

    # Восстанавливаем очереди из БД
    try:
        players_in_queue = db_manager.fetchall(
            "players", "SELECT playername, discordid FROM players WHERE in_queue = 1"
        )
    except Exception as e:
        print(f"[INIT] Ошибка восстановления очереди: {e}")

    # Создаем фоновую задачу
    bot.loop.create_task(check_expired_matches(bot))

    # Проверяем и создаём необходимые роли/каналы
    for guild in bot.guilds:
        queue_channel = discord.utils.get(guild.text_channels, name="elobot-queue")
        results_channel = discord.utils.get(guild.text_channels, name="elobot-results")

        if not queue_channel:
            print(f"⚠️ На сервере '{guild.name}' нет канала 'elobot-queue'")
        if not results_channel:
            print(f"⚠️ На сервере '{guild.name}' нет канала 'elobot-results'")

        verify_channel = discord.utils.get(guild.text_channels, name="elobot-verify")
        logs_channel = discord.utils.get(guild.text_channels, name="elobot-logs")

        if not verify_channel:
            print(f"⚠️ На сервере '{guild.name}' нет канала 'elobot-verify'")
        if not logs_channel:
            print(f"⚠️ На сервере '{guild.name}' нет канала 'elobot-logs'")


bot.remove_command("help")


@bot.command()
async def help(ctx):
    """Показывает подробное руководство по командам бота"""
    embed = discord.Embed(
        title="📚 Полное руководство по командам ELO Bot",
        description="Все доступные команды для работы с системой рейтинга",
        color=discord.Color.blue(),
    )

    # Основные команды
    embed.add_field(
        name="🎮 Основные команды",
        value=(
            "`.play` - Начать поиск матча (доступно в elobot-queue)\n"
            "`.leave` - Покинуть очередь (доступно в elobot-queue)\n"
            "`.giveup` - Сдаться в текущем матче\n"
            "`.queue` - Показать состояние очередей (доступно в elobot-queue)\n"
            "`.report <ID матча> <причина>` - Пожаловаться на матч"
        ),
        inline=False,
    )

    # Статистика
    embed.add_field(
        name="📊 Статистика и информация",
        value=(
            "`.playerinfo <ник>` - Полная статистика игрока\n"
            "`.leaderboard` - Топ игроков с фильтрами по режимам\n"
            "`.matchinfo <ID>` - Информация о конкретном матче"
        ),
        inline=False,
    )

    # Верификация
    embed.add_field(
        name="🔐 Верификация",
        value=(
            "Для верификации отправьте в канал #elobot-verify:\n"
            "1. Ваш игровой ник\n"
            "2. Скриншот вашего профиля в игре\n\n"
            "После проверки модератором вы получите доступ ко всем функциям"
        ),
        inline=False,
    )

    # Отправка результатов
    embed.add_field(
        name="📨 Отправка результатов",
        value=(
            "**Победитель** должен отправить в ЛС боту:\n"
            "`.result <ID матча> <счет>` с приложенным скриншотом\n"
            "Пример: `.result 42 5-3`\n\n"
            "❗ Первое число - счет победителя, второе - проигравшего"
        ),
        inline=False,
    )

    # Системная информация
    embed.add_field(
        name="⚙️ Системная информация",
        value=(
            "• Матчи автоматически завершаются через 1 час\n"
            "• ELO пересчитывается после каждого подтвержденного матча\n"
            "• За нарушения назначаются технические поражения\n"
            "• Спорные ситуации решаются модераторами"
        ),
        inline=False,
    )

    # Поддержка
    embed.add_field(
        name="🆘 Поддержка",
        value=("По всем вопросам, предложениям и ошибкам обращайтесь к @postm1rage\n"),
        inline=False,
    )

    embed.set_footer(text=f"Запрошено пользователем {ctx.author.display_name}")

    # Добавляем кнопки для быстрого доступа к каналам
    view = discord.ui.View()
    view.add_item(
        discord.ui.Button(
            label="Канал очереди",
            style=discord.ButtonStyle.link,
            url=f"https://discord.com/channels/{ctx.guild.id}/{discord.utils.get(ctx.guild.channels, name='elobot-queue').id}",
        )
    )
    view.add_item(
        discord.ui.Button(
            label="Канал верификации",
            style=discord.ButtonStyle.link,
            url=f"https://discord.com/channels/{ctx.guild.id}/{discord.utils.get(ctx.guild.channels, name='elobot-verify').id}",
        )
    )
    view.add_item(
        discord.ui.Button(
            label="Канал результатов",
            style=discord.ButtonStyle.link,
            url=f"https://discord.com/channels/{ctx.guild.id}/{discord.utils.get(ctx.guild.channels, name='elobot-results').id}",
        )
    )

    await ctx.send(embed=embed, view=view)


@bot.command()
async def playerinfo(ctx, nickname: str):
    """Показывает информацию об игроке"""
    player = db_manager.fetchone(
        "players",
        """
        SELECT playerid, playername, currentelo, 
               elo_station5f, elo_mots, elo_12min,
               wins, losses, ties
        FROM players
        WHERE playername = ?
        """,
        (nickname,),
    )

    if player:
        total_matches = player[6] + player[7] + player[8]  # wins + losses + ties

        player_data = {
            "id": player[0],
            "name": player[1],
            "elo": player[2],
            "elo_station5f": player[3],
            "elo_mots": player[4],
            "elo_12min": player[5],
            "wins": player[6],
            "losses": player[7],
            "ties": player[8],
            "matches": total_matches,
        }

        embed = discord.Embed(
            title=f"Информация об игроке {player_data['name']}",
            color=discord.Color.blue(),
        )
        embed.add_field(name="ID", value=player_data["id"], inline=True)
        embed.add_field(name="Общий ELO", value=player_data["elo"], inline=True)
        embed.add_field(
            name="ELO Station", value=player_data["elo_station5f"], inline=True
        )
        embed.add_field(name="ELO MotS", value=player_data["elo_mots"], inline=True)
        embed.add_field(name="ELO 12min", value=player_data["elo_12min"], inline=True)
        embed.add_field(name="Победы", value=player_data["wins"], inline=True)
        embed.add_field(name="Поражения", value=player_data["losses"], inline=True)
        embed.add_field(name="Ничьи", value=player_data["ties"], inline=True)
        embed.add_field(name="Всего матчей", value=player_data["matches"], inline=True)

        await ctx.send(embed=embed)
    else:
        await ctx.send(f"Игрок с ником '{nickname}' не найден")


@bot.command()
async def leaderboard(ctx):
    """Показывает таблицу лидеров с кнопками выбора режима"""
    await send_leaderboard(ctx, "overall")


async def send_leaderboard(source, mode_key):
    """Универсальная функция отправки/обновления лидерборда"""
    elo_col, wins_col, losses_col, ties_col = LEADERBOARD_MODES[mode_key]

    leaders = db_manager.fetchall(
        "players",
        f"""
        SELECT playername, {elo_col}, {wins_col}, {losses_col}, {ties_col}
        FROM players 
        ORDER BY {elo_col} DESC 
        LIMIT 10
        """,
    )

    mode_names = {
        "overall": "Общий рейтинг",
        "station5flags": "Station 5 Flags",
        "mots": "MotS Solo",
        "12min": "12 Minute",
    }

    embed = discord.Embed(
        title=f"🏆 Топ-10 игроков: {mode_names[mode_key]}",
        color=discord.Color.gold(),
    )

    for i, (name, elo, wins, losses, ties) in enumerate(leaders, 1):
        total = wins + losses + ties
        winrate = (wins / total * 100) if total > 0 else 0

        embed.add_field(
            name=f"{i}. {name}",
            value=(
                f"ELO: {elo}\n"
                f"Победы: {wins} | Поражения: {losses} | Ничьи: {ties}\n"
                f"Винрейт: {winrate:.1f}%"
            ),
            inline=False,
        )

    embed.set_footer(text=f"Обновлено: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    view = LeaderboardView(mode_key)

    if isinstance(source, discord.Interaction):
        view.message = source.message
        await source.message.edit(embed=embed, view=view)
    else:
        source.leaderboard_message = await source.send(embed=embed, view=view)


@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # Логирование входящего сообщения
    channel_info = (
        f"ЛС бота"
        if isinstance(message.channel, discord.DMChannel)
        else f"#{message.channel.name}"
    )
    print(f"[Сообщение] {message.author} ({channel_info}): {message.content[:100]}...")

    # Обработка команд в ЛС
    if isinstance(message.channel, discord.DMChannel):
        ctx = await bot.get_context(message)
        if ctx.command:
            await bot.invoke(ctx)
        return

    await bot.process_commands(message)

    # Обработка результатов матчей в канале elobot-results
    if message.channel.name == "elobot-results" and message.attachments:
        score_match = re.search(r"(\d+)\s*-\s*(\d+)", message.content)
        if score_match:
            score1 = int(score_match.group(1))
            score2 = int(score_match.group(2))

            if score1 == score2:
                await message.channel.send(
                    "❌ Счет не может быть равным! Матч должен иметь победителя."
                )
                return

        winner_score = max(score1, score2)
        loser_score = min(score1, score2)
        is_player1_winner = score1 > score2

        player_data = db_manager.fetchone(
            "players",
            "SELECT playername FROM players WHERE discordid = ?",
            (str(message.author.id),),
        )
        if not player_data:
            await message.channel.send("❌ Вы не зарегистрированы в системе")
            return

        nickname = player_data[0]

        match_data = db_manager.fetchone(
            "matches",
            """
            SELECT matchid, player1, player2, mode 
            FROM matches 
            WHERE (player1 = ? OR player2 = ?) 
            AND isover = 0
            """,
            (nickname, nickname),
        )

        if not match_data:
            await message.channel.send("❌ Активный матч не найден")
            return

        match_id, player1, player2, mode = match_data

        if is_player1_winner and nickname != player1:
            await message.channel.send(
                f"❌ Результат должен отправлять победитель ({player1})!"
            )
            return
        elif not is_player1_winner and nickname != player2:
            await message.channel.send(
                f"❌ Результат должен отправлять победитель ({player2})!"
            )
            return

        if nickname == player1:
            player1_score, player2_score = score1, score2
            winner, loser = player1, player2
        else:
            player1_score, player2_score = score2, score1
            winner, loser = player2, player1

        db_manager.execute(
            "matches",
            """
            UPDATE matches 
            SET player1score = ?, player2score = ?
            WHERE matchid = ?
            """,
            (player1_score, player2_score, match_id),
        )

        # Обновляем статистику
        if player1_score > player2_score:
            db_manager.execute(
                "players",
                "UPDATE players SET wins = wins + 1 WHERE playername = ?",
                (winner,),
            )
            db_manager.execute(
                "players",
                "UPDATE players SET losses = losses + 1 WHERE playername = ?",
                (loser,),
            )

            if mode == MODES["station5f"]:
                db_manager.execute(
                    "players",
                    "UPDATE players SET wins_station5f = wins_station5f + 1 WHERE playername = ?",
                    (winner,),
                )
                db_manager.execute(
                    "players",
                    "UPDATE players SET losses_station5f = losses_station5f + 1 WHERE playername = ?",
                    (loser,),
                )
            elif mode == MODES["mots"]:
                db_manager.execute(
                    "players",
                    "UPDATE players SET wins_mots = wins_mots + 1 WHERE playername = ?",
                    (winner,),
                )
                db_manager.execute(
                    "players",
                    "UPDATE players SET losses_mots = losses_mots + 1 WHERE playername = ?",
                    (loser,),
                )
            elif mode == MODES["12min"]:
                db_manager.execute(
                    "players",
                    "UPDATE players SET wins_12min = wins_12min + 1 WHERE playername = ?",
                    (winner,),
                )
                db_manager.execute(
                    "players",
                    "UPDATE players SET losses_12min = losses_12min + 1 WHERE playername = ?",
                    (loser,),
                )

        elif player1_score < player2_score:
            db_manager.execute(
                "players",
                "UPDATE players SET wins = wins + 1 WHERE playername = ?",
                (winner,),
            )
            db_manager.execute(
                "players",
                "UPDATE players SET losses = losses + 1 WHERE playername = ?",
                (loser,),
            )

            if mode == MODES["station5f"]:
                db_manager.execute(
                    "players",
                    "UPDATE players SET wins_station5f = wins_station5f + 1 WHERE playername = ?",
                    (winner,),
                )
                db_manager.execute(
                    "players",
                    "UPDATE players SET losses_station5f = losses_station5f + 1 WHERE playername = ?",
                    (loser,),
                )
            elif mode == MODES["mots"]:
                db_manager.execute(
                    "players",
                    "UPDATE players SET wins_mots = wins_mots + 1 WHERE playername = ?",
                    (winner,),
                )
                db_manager.execute(
                    "players",
                    "UPDATE players SET losses_mots = losses_mots + 1 WHERE playername = ?",
                    (loser,),
                )
            elif mode == MODES["12min"]:
                db_manager.execute(
                    "players",
                    "UPDATE players SET wins_12min = wins_12min + 1 WHERE playername = ?",
                    (winner,),
                )
                db_manager.execute(
                    "players",
                    "UPDATE players SET losses_12min = losses_12min + 1 WHERE playername = ?",
                    (loser,),
                )
        else:
            db_manager.execute(
                "players",
                "UPDATE players SET ties = ties + 1 WHERE playername IN (?, ?)",
                (player1, player2),
            )

            if mode == MODES["station5f"]:
                db_manager.execute(
                    "players",
                    "UPDATE players SET ties_station5f = ties_station5f + 1 WHERE playername IN (?, ?)",
                    (player1, player2),
                )
            elif mode == MODES["mots"]:
                db_manager.execute(
                    "players",
                    "UPDATE players SET ties_mots = ties_mots + 1 WHERE playername IN (?, ?)",
                    (player1, player2),
                )
            elif mode == MODES["12min"]:
                db_manager.execute(
                    "players",
                    "UPDATE players SET ties_12min = ties_12min + 1 WHERE playername IN (?, ?)",
                    (player1, player2),
                )

        moderator = await bot.fetch_user(MODERATOR_ID)
        embed = discord.Embed(
            title="⚠️ Требуется подтверждение матча",
            description=(
                f"**Match ID:** {match_id}\n"
                f"**Режим:** {MODE_NAMES.get(mode, 'Unknown')}\n"
                f"**{player1}** vs **{player2}**\n"
                f"**Счет:** {player1_score}-{player2_score}"
            ),
            color=discord.Color.orange(),
        )

        view = ConfirmMatchView(match_id, bot, message.id)

        await moderator.send(
            embed=embed,
            view=view,
            files=[await attachment.to_file() for attachment in message.attachments],
        )

        try:
            await message.delete()
            print(f"Сообщение с результатом матча удалено: {message.id}")
        except Exception as e:
            print(f"Ошибка при удалении сообщения: {e}")
        return

    await bot.process_commands(message)


@bot.event
async def on_disconnect():
    db_manager.close_all()
    print("Соединения с БД закрыты")


# Настройка модулей
setup_queueing(bot)
setup_verification(bot)
setup_nickname_updater(bot)
setup_role_manager(bot)
setup_ban(bot)
tournament_test.setup(bot)


@bot.check
async def globally_check_ban(ctx):
    # Разрешаем команды в этих каналах без проверки
    if ctx.channel.name in ["elobot-verify", "elobot-logs"]:
        return True

    # Разрешаем команды модератору
    if ctx.author.id == MODERATOR_ID:
        return True

    player = db_manager.fetchone(
        "players",
        "SELECT isbanned FROM players WHERE discordid = ?",
        (str(ctx.author.id),),
    )

    if player and player[0] == 1:  # Если isbanned == 1
        await ctx.send("⛔ Вы забанены и не можете использовать команды бота.")
        return False
    return True


async def load_extensions():
    await bot.load_extension("tournaments")


@bot.event
async def setup_hook():
    bot.loop.create_task(find_match())
    bot.loop.create_task(check_expired_matches(bot))
    await load_extensions()


bot.run(token, log_handler=handler, log_level=logging.DEBUG)
