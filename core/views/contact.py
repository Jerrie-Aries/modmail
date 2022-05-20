from __future__ import annotations

import asyncio
import re

from typing import Optional, Tuple, Union, TYPE_CHECKING

import discord
from discord import ButtonStyle, Interaction
from discord.ui import Button, View
from emoji import UNICODE_EMOJI_ENGLISH

from core.enums_ext import DMDisabled


if TYPE_CHECKING:
    from bot import ModmailBot

MISSING = discord.utils.MISSING


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
        await interaction.response.send_message("Done", ephemeral=True)


class ContactView(View):
    """
    Represents a persistent view for contact panel.

    Parameters
    -----------
    bot : ModmailBot
        The Modmail bot.
    message : discord.Message
        The message object containing the view the bot listens to.

    """

    def __init__(self, bot: ModmailBot, message: discord.Message = MISSING):
        self.bot: ModmailBot = bot
        super().__init__(timeout=None)

        if message:
            item = {
                "label": self.bot.config["contact_button_label"],
                "emoji": self._resolve_emoji(self.bot.config["contact_button_emoji"]),
                "style": ButtonStyle.grey,
                "custom_id": f"contactbutton-{self.bot.user.id}-{message.channel.id}-{message.id}",
            }
            self.add_item(ContactButton(item))
        else:
            # this is for startup event
            # TODO: check whether this can be completely ommited
            self._setup_persistent()

    def _setup_persistent(self) -> None:
        channel_id, message_id = self._resolve_ids()
        if not channel_id or not message_id:
            return
        item = {
            "label": self.bot.config["contact_button_label"],
            "emoji": self._resolve_emoji(self.bot.config["contact_button_emoji"]),
            "style": ButtonStyle.grey,
            "custom_id": f"contactbutton-{self.bot.user.id}-{channel_id}-{message_id}",
        }
        self.add_item(ContactButton(item))

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

    def _resolve_ids(self) -> Tuple[Optional[int], Optional[int]]:
        id_string = self.bot.config["contact_panel_message"]
        if id_string is None:
            return None, None

        # copied from discord.py PartialMessageConverter._get_id_matches
        id_regex = re.compile(
            r"(?P<channel_id>[0-9]{15,20})-(?P<message_id>[0-9]{15,20})$"
        )
        match = id_regex.match(id_string)
        if match is None:
            return None, None

        data = match.groupdict()
        channel_id = int(data["channel_id"])
        message_id = int(data["message_id"])

        return channel_id, message_id

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.bot:
            return False
        # TODO: Check if user is blocked
        if self.bot.config["dm_disabled"] in (
            DMDisabled.NEW_THREADS,
            DMDisabled.ALL_THREADS,
        ):
            embed = discord.Embed(
                color=self.bot.error_color,
                description=self.bot.config["disabled_new_thread_response"],
            )
            embed.set_footer(
                text=self.bot.config["disabled_new_thread_footer"],
                icon_url=self.bot.guild.icon.url,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return False
        return self.bot.guild.get_member(interaction.user.id) is not None
