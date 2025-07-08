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
    
    async def create_tournament_channels(self, guild, name):
        """Создает ветку каналов для турнира"""
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            guild.me: discord.PermissionOverwrite(read_messages=True)
        }
        
        # Создаем категорию
        category = await guild.create_category(name, overwrites=overwrites)
        
        # Создаем каналы
        info_ch = await guild.create_text_channel(f"{name}-info", category=category)
        results_ch = await guild.create_text_channel(f"{name}-results", category=category)
        matches_ch = await guild.create_text_channel(f"{name}-matches", category=category)
        register_ch = await guild.create_text_channel(f"{name}-register", category=category)
        
        return {
            "category": category,
            "info": info_ch,
            "results": results_ch,
            "matches": matches_ch,
            "register": register_ch,
            "participants": [],
            "banned": [],
            "started": False
        }

    @commands.command()
    @commands.has_any_role(MODERATOR_ID)
    async def tournament(self, ctx, name: str, slots: int):
        """Создает новый турнир"""
        if slots not in [8, 16, 32, 64]:
            return await ctx.send("❌ Число участников должно быть 8, 16, 32 или 64")
            
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
        
        await ctx.send(f"✅ Турнир '{name}' создан! Регистрация в {register_ch.mention}")

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return
            
        # Проверяем, что сообщение в канале регистрации турнира
        if not message.channel.name.endswith("-register"):
            return
            
        # Получаем название турнира
        tournament_name = message.channel.name.replace("-register", "")
        
        if tournament_name not in self.tournaments:
            return
            
        # Удаляем все сообщения без команды register
        if not message.content.startswith(".register"):
            try:
                await message.delete()
            except:
                pass
            return
            
        # Обработка команды register
        await self.process_registration(message, tournament_name)

    async def process_registration(self, message, tournament_name):
        """Обрабатывает регистрацию на турнир"""
        user = message.author
        tournament = self.tournaments[tournament_name]
        
        # Проверка условий
        checks = {
            "already_registered": user.id in [p["id"] for p in tournament["participants"]],
            "banned_in_bot": self.is_user_banned(user.id),
            "banned_in_tournament": user.id in tournament["banned"],
            "blacklisted": self.is_user_blacklisted(user.id)
        }
        
        if any(checks.values()):
            # Определяем причину отказа
            reason = (
                "❌ Вы уже зарегистрированы" if checks["already_registered"] else
                "❌ Вы забанены в боте" if checks["banned_in_bot"] else
                "❌ Вы забанены в этом турнире" if checks["banned_in_tournament"] else
                "❌ Вы в черном списке турниров"
            )
            
            await message.add_reaction("❌")
            try:
                await user.send(reason)
            except:
                pass
            return
            
        # Проверяем верификацию
        player_data = db_manager.fetchone(
            "players",
            "SELECT playername, isbanned FROM players WHERE discordid = ?",
            (str(user.id),)
        )
            
        if not player_data or player_data[1] == 1:
            await message.add_reaction("❌")
            try:
                await user.send("❌ Вы не верифицированы или забанены в боте")
            except:
                pass
            return
            
        # Регистрируем игрока
        tournament["participants"].append({
            "id": user.id,
            "name": player_data[0],
            "mention": user.mention
        })
        
        await message.add_reaction("✅")
        
        # Обновляем списки
        await self.update_lists(tournament_name)

    async def update_lists(self, tournament_name):
        """Обновляет списки участников, забаненных и черный список"""
        tournament = self.tournaments[tournament_name]
        
        participants = "\n".join([p["mention"] for p in tournament["participants"]]) or "Пусто"
        banned = "\n".join([f"<@{uid}>" for uid in tournament["banned"]]) or "Пусто"
        blacklisted = "\n".join([f"<@{uid}>" for uid in self.get_blacklist()]) or "Пусто"
        
        await tournament["participants_msg"].edit(content=f"**Участники турнира:**\n{participants}")
        await tournament["banned_msg"].edit(content=f"**Забаненные игроки:**\n{banned}")
        await tournament["blacklist_msg"].edit(content=f"**Черный список:**\n{blacklisted}")

    # Команды модератора
    @commands.command()
    @commands.has_any_role(MODERATOR_ID)
    async def tban(self, ctx, member: discord.Member):
        """Бан игрока в текущем турнире"""
        tournament_name = ctx.channel.name.replace("-register", "")
        
        if tournament_name not in self.tournaments:
            return await ctx.send("❌ Это не канал регистрации турнира")
            
        if member.id in self.tournaments[tournament_name]["banned"]:
            return await ctx.send("❌ Игрок уже забанен в этом турнире")
            
        self.tournaments[tournament_name]["banned"].append(member.id)
        
        # Удаляем из участников если был зарегистрирован
        self.tournaments[tournament_name]["participants"] = [
            p for p in self.tournaments[tournament_name]["participants"] 
            if p["id"] != member.id
        ]
        
        await self.update_lists(tournament_name)
        await ctx.send(f"✅ {member.mention} забанен в турнире")

    @commands.command()
    @commands.has_any_role(MODERATOR_ID)
    async def untban(self, ctx, member: discord.Member):
        """Разбан игрока в текущем турнире"""
        tournament_name = ctx.channel.name.replace("-register", "")
        
        if tournament_name not in self.tournaments:
            return await ctx.send("❌ Это не канал регистрации турнира")
            
        if member.id not in self.tournaments[tournament_name]["banned"]:
            return await ctx.send("❌ Игрок не забанен в этом турнире")
            
        self.tournaments[tournament_name]["banned"].remove(member.id)
        await self.update_lists(tournament_name)
        await ctx.send(f"✅ {member.mention} разбанен в турнире")

    @commands.command()
    @commands.has_any_role(MODERATOR_ID)
    async def blacklist(self, ctx, member: discord.Member):
        """Добавление в черный список турниров"""
        if self.add_to_blacklist(member.id):
            await ctx.send(f"✅ {member.mention} добавлен в черный список турниров")
        else:
            await ctx.send(f"❌ {member.mention} уже в черном списке")

    @commands.command()
    @commands.has_any_role(MODERATOR_ID)
    async def unblacklist(self, ctx, member: discord.Member):
        """Удаление из черного списка турниров"""
        if self.remove_from_blacklist(member.id):
            await ctx.send(f"✅ {member.mention} удален из черного списка турниров")
        else:
            await ctx.send(f"❌ {member.mention} не найден в черном списке")

    @commands.command()
    @commands.has_any_role(MODERATOR_ID)
    async def tstart(self, ctx):
        """Начало турнира"""
        tournament_name = ctx.channel.name.replace("-register", "")
        
        if tournament_name not in self.tournaments:
            return await ctx.send("❌ Это не канал регистрации турнира")
            
        tournament = self.tournaments[tournament_name]
        
        if tournament["started"]:
            return await ctx.send("❌ Турнир уже начат")
            
        # Добавляем пустые слоты если нужно
        required_slots = int(tournament_name.split("-")[-1])  # Извлекаем число из названия
        current_participants = len(tournament["participants"])
        
        if current_participants < required_slots:
            for i in range(required_slots - current_participants):
                tournament["participants"].append({
                    "id": 0,
                    "name": f"emptyslot{i+1}",
                    "mention": f"emptyslot{i+1}"
                })
        
        tournament["started"] = True
        await ctx.send("✅ Турнир начат!")
        await self.update_lists(tournament_name)

    # Вспомогательные методы для работы с черным списком
    def add_to_blacklist(self, user_id):
        """Добавляет пользователя в черный список"""
        # Реализация хранения черного списка (можно использовать БД)
        pass
        
    def remove_from_blacklist(self, user_id):
        """Удаляет пользователя из черного списка"""
        pass
        
    def is_user_blacklisted(self, user_id):
        """Проверяет, находится ли пользователь в черном списке"""
        return False
        
    def is_user_banned(self, user_id):
        """Проверяет, забанен ли пользователь в боте"""
        player = db_manager.fetchone(
            "players",
            "SELECT isbanned FROM players WHERE discordid = ?",
            (str(user_id),)
        )
        return player and player[0] == 1
        
    def get_blacklist(self):
        """Возвращает список ID пользователей в черном списке"""
        return []

async def setup(bot):
    await bot.add_cog(Tournaments(bot))