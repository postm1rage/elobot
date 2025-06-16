import discord
from discord.ext import commands
import logging
from dotenv import load_dotenv
import os

load_dotenv()
token = os.getenv("DISCORD_TOKEN")

handler = logging.FileHandler(filename="discord.log", encoding="utf-8", mode="w")
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(
    intents=intents,
    command_prefix="!",
)


@bot.event
async def on_ready():
    print("Йоу")


@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    if message.content == "Йоу":
        await message.channel.send("ЙОООООООООУ")

    if message.content == "Z":
        await message.channel.send("V")


bot.run(token, log_handler=handler, log_level=logging.DEBUG)
