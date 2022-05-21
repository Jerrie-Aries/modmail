from __future__ import annotations

import re

from typing import List, Optional, TypedDict, Union, TYPE_CHECKING

import discord
from discord import ButtonStyle, Interaction
from discord.ui import Button, View
from emoji import UNICODE_EMOJI_ENGLISH


if TYPE_CHECKING:
    from bot import ModmailBot

    class ContactButtonPayload(TypedDict):
        label: str
        emoji: str
        style: ButtonStyle
        custom_id: str


MISSING = discord.utils.MISSING


class ContactButton(Button["ContactView"]):
    """
    The contact button.
    """

    def __init__(self, payload: ContactButtonPayload):
        super().__init__(
            label=payload["label"],
            emoji=payload["emoji"],
            style=ButtonStyle.grey,
            custom_id=payload["custom_id"],
        )

    async def callback(self, interaction: Interaction):
        assert self.view is not None
        await self.view.bot.handle_contact_panel_events(interaction=interaction)
        await interaction.response.defer()


class ContactView(View):
    """
    Represents a persistent view for contact panel.

    This view can only be added to the bot's message (discord limitation)
    and in the main guild.

    Parameters
    -----------
    bot : ModmailBot
        The Modmail bot.
    message : discord.Message
        The message object containing the view the bot listens to.

    """

    children: List[ContactButton]

    def __init__(self, bot: ModmailBot, message: discord.Message = MISSING):
        self.bot: ModmailBot = bot
        super().__init__(timeout=None)

        if message:
            payload: ContactButtonPayload = {
                "label": self.bot.config["contact_button_label"],
                "emoji": self._resolve_emoji(self.bot.config["contact_button_emoji"]),
                "style": ButtonStyle.grey,
                "custom_id": f"contactbutton-{self.bot.user.id}-{message.channel.id}-{message.id}",
            }
            self.add_item(ContactButton(payload))

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

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.bot:
            return False

        # TODO: Cooldown check to prevent spam.

        return self.bot.guild.get_member(interaction.user.id) is not None

    async def force_stop(self) -> None:
        """
        Stops listening to interactions made on this view and removes the view from the message.
        """
        self.stop()

        if self.channel_id and self.message_id:
            channel = self.bot.guild.get_channel(self.channel_id)
            if channel is not None:
                message = await channel.fetch_message(self.message_id)
                await message.edit(view=None)
