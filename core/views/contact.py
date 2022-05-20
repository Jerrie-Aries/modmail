from __future__ import annotations

import asyncio
import re

from typing import Optional, Union, TYPE_CHECKING

import discord
from discord import ButtonStyle, Interaction
from discord.ui import Button, View
from emoji import UNICODE_EMOJI_ENGLISH

from core.logging_ext import getLogger


if TYPE_CHECKING:
    from bot import ModmailBot

MISSING = discord.utils.MISSING

logger = getLogger(__name__)


class ContactButton(Button["ContactView"]):
    """
    The contact button.
    """

    def __init__(self, item):
        super().__init__(
            label=item["label"],
            emoji=item["emoji"],
            style=ButtonStyle.grey,
            custom_id=item["custom_id"],
        )

    async def callback(self, interaction: Interaction):
        assert self.view is not None
        pass


class ContactView(View):
    """
    Represents press to contact persistent view.

    Parameters
    -----------
    bot : ModmailBot
        The Modmail bot.
    message : discord.Message
        The message object containing the view the bot listens to.

    """

    def __init__(self, bot: ModmailBot, message: discord.Message = MISSING):
        self.bot: ModmailBot = bot
        self.message: discord.Message = message
        super().__init__(timeout=None)

        asyncio.create_task(self.initialize())

    async def initialize(self) -> None:
        if self.message is MISSING:
            message = await self.fetch_contact_message()
            if message is None:
                return
            self.message = message

        item = {
            "label": self.bot.config["contact_button_label"],
            "emoji": self._resolve_emoji(self.bot.config["contact_button_emoji"]),
            "style": ButtonStyle.grey,
            "custom_id": f"contactbutton-{self.bot.user.id}-{self.message.channel.id}-{self.message.id}",
        }
        self.add_item(ContactButton(item))
        await self.message.edit(view=self)

    def _resolve_emoji(
        self, name: Optional[str]
    ) -> Optional[Union[discord.PartialEmoji, discord.Emoji, str]]:
        if name is None:
            return None

        name = re.sub("\ufe0f", "", name)
        emoji = discord.PartialEmoji.from_str(name)
        if emoji.is_unicode_emoji():
            if emoji.name not in UNICODE_EMOJI_ENGLISH:
                emoji = None
        else:
            # custom emoji
            emoji = self.bot.get_emoji(emoji.id)

        if emoji is None:
            raise ValueError(f'Emoji "{name}" not found.')

        return emoji

    async def fetch_contact_message(self) -> Optional[discord.Message]:
        id_string = self.bot.config.get["contact_message_panel"]
        if id_string is None:
            return None

        # copied from discord.py PartialMessageConverter._get_id_matches
        id_regex = re.compile(
            r"(?P<channel_id>[0-9]{15,20})-(?P<message_id>[0-9]{15,20})$"
        )
        match = id_regex.match(id_string)
        if match is None:
            return None

        data = match.groupdict()
        # only get from main guild
        channel = self.bot.guild.get_channel(int(data["channel_id"]))
        if channel is None:
            return None

        try:
            message = await channel.fetch_message(int(data["message_id"]))
        except discord.NotFound:
            logger.error(f'Contact message "{id_string}" not found.')
            return None

        return message

    async def interaction_check(self, interaction: Interaction) -> bool:
        if self.message is MISSING or interaction.user.bot:
            return False
        # TODO: Check if user is blocked
        return self.message.guild.get_member(interactin.user.id) is not None
