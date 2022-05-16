from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from core.timeutils import datetime_formatter
from core.utils import code_block

if TYPE_CHECKING:
    from ...bot import ModmailBot

    from core.ext.commands import Context


class BotResource:
    def __init__(self, ctx: Context, bot: ModmailBot):
        self.ctx: Context = ctx
        self.bot: ModmailBot = bot

    def bot_embed(self) -> discord.Embed:
        """Create an embed containing the bot's information."""
        bot_me: discord.Member = self.ctx.me

        embed = discord.Embed(color=bot_me.color)

        embed.set_author(name=f"{bot_me}")

        embed.add_field(
            name="Prefix:", value=f"`{self.bot.prefix}` or {self.bot.user.mention}"
        )
        embed.add_field(
            name="Created:",
            value=datetime_formatter.format_dt(self.bot.user.created_at),
        )
        embed.add_field(
            name="Age:", value=datetime_formatter.age(self.bot.user.created_at)
        )
        embed.add_field(
            name="Latency:", value=code_block(f"{self.bot.latency * 1000:.2f} ms", "py")
        )
        embed.add_field(name="Uptime:", value=code_block(self.bot.uptime, "cs"))
        embed.add_field(
            name="Hosting Method:",
            value=code_block(self.bot.hosting_method.name, "fix"),
        )
        embed.add_field(name="Bot Version:", value=code_block(self.bot.version, "py"))
        embed.add_field(
            name="Python Version:", value=code_block(self.bot.python_version, "py")
        )
        embed.add_field(
            name="discord.py Version:", value=code_block(discord.__version__, "py")
        )

        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.set_footer(text=f"Bot ID: {self.bot.user.id}")

        return embed
