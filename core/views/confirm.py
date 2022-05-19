from __future__ import annotations

import re

from typing import List, Optional, Tuple, Union, TYPE_CHECKING

import discord
from discord import ButtonStyle, Interaction
from discord.ui import Button, View
from emoji import UNICODE_EMOJI_ENGLISH

if TYPE_CHECKING:
    from bot import ModmailBot

    # these are for the sake of type hints only,
    # so no need to execute these in runtime

    from ..types_ext.button_map import (
        ConfirmationButtonItem,
        ConfirmationButtonCallback,
    )

MISSING = discord.utils.MISSING


class ConfirmationButton(Button["ConfirmView"]):
    """
    Represents an instance of button component for ConfirmView.

    Parameters
    -----------
    item : ConfirmationButtonItem
        The raw dictionary of button item which contains `label`, `style`, `emoji` and `action` keys.
    """

    def __init__(self, item: ConfirmationButtonItem):
        super().__init__(label=item["label"], emoji=item["emoji"], style=item["style"])

        self._button_callback: ConfirmationButtonCallback = item["callback"]

    async def callback(self, interaction: Interaction):
        assert self.view is not None
        self.view.interaction = interaction
        await self._button_callback(self, interaction)


class ConfirmView(View):
    """
    Confirmation views. This can be used to add buttons on confirmation messages.

    Users can only select one of the `Yes` and `No` buttons on this view.
    After one of them is selected, the view will stop which means the bot will no longer listen to
    interactions on this view, and the buttons will be disabled.

    Parameters
    -----------
    bot : ModmailBot
        The Modmail bot.
    user : Union[discord.Member, discord.User]
        The author that triggered this confirmation view.
    timeout : float
        Time before this view timed out. Defaults to `20` seconds.
    """

    children: List[ConfirmationButton]

    def __init__(
        self,
        bot: ModmailBot,
        user: Union[discord.Member, discord.User],
        timeout: float = 20.0,
    ):
        self.bot: ModmailBot = bot
        self.user: Union[discord.Member, discord.User] = user
        super().__init__(timeout=timeout)

        accept_label, accept_emoji = self._resolve_label_and_emoji(
            self.bot.config["confirm_button_accept"]
        )
        deny_label, deny_emoji = self._resolve_label_and_emoji(
            self.bot.config["confirm_button_deny"]
        )
        self.button_map: List[ConfirmationButtonItem] = [
            {
                "label": accept_label,
                "emoji": accept_emoji,
                "style": ButtonStyle.green,
                "callback": self._action_confirm,
            },
            {
                "label": deny_label,
                "emoji": deny_emoji,
                "style": ButtonStyle.red,
                "callback": self._action_cancel,
            },
        ]

        self._message: discord.Message = MISSING
        self.value: Optional[bool] = None
        self.interaction: discord.Interaction = MISSING
        self._selected_button: ConfirmationButton = MISSING

        for item in self.button_map:
            self.add_item(ConfirmationButton(item))

    def _resolve_label_and_emoji(
        self, name: str
    ) -> Tuple[
        Optional[str], Optional[Union[discord.PartialEmoji, discord.Emoji, str]]
    ]:
        name = re.sub("\ufe0f", "", name)  # remove trailing whitespace
        emoji = discord.PartialEmoji.from_str(name)
        label = None
        if emoji.is_unicode_emoji():
            if emoji.name not in UNICODE_EMOJI_ENGLISH:
                label = emoji.name
        else:
            # custom emoji
            emoji = self.bot.get_emoji(emoji.id)
            if emoji is None:
                raise ValueError(f'Emoji "{name}" not found.')

        if label:
            return label, None
        return None, emoji

    @property
    def message(self) -> discord.Message:
        """
        Returns `discord.Message` object for this instance, or `MISSING` if it has never been set.

        This property must be set manually. If it hasn't been set after instantiating the view,
        consider using:
            `view.message = await ctx.send(content="Content.", view=view)`
        """
        return self._message

    @message.setter
    def message(self, item: discord.Message):
        """
        Manually set the `message` attribute for this instance.

        With this attribute set, the view for the message will be automatically updated after
        times out.
        """
        if not isinstance(item, discord.Message):
            raise TypeError(
                f"Invalid type. Expected `Message`, got `{type(item).__name__}` instead."
            )

        self._message = item

    async def interaction_check(self, interaction: Interaction) -> bool:
        return (
            self.message is not MISSING
            and self.message.id == interaction.message.id
            and self.user.id == interaction.user.id
        )

    async def on_timeout(self) -> None:
        self.update_view()
        if self.message:
            await self.message.edit(view=self)

    async def _action_confirm(self, button: Button, interaction: Interaction):
        """
        Executed when the user presses the `confirm` button.
        """
        self._selected_button = button
        self.value = True
        await self.disable_and_stop(interaction)

    async def _action_cancel(self, button: Button, interaction: Interaction):
        """
        Executed when the user presses the `cancel` button.
        """
        self._selected_button = button
        self.value = False
        await self.disable_and_stop(interaction)

    async def disable_and_stop(self, interaction: Interaction):
        """
        Method to disable buttons and stop the view after an interaction is made.
        """
        self.update_view()
        await interaction.response.edit_message(view=self)
        if not self.is_finished():
            self.stop()

    def update_view(self):
        """
        Disables the buttons on the view. Unselected button will be greyed out.
        """
        for child in self.children:
            child.disabled = True
            if self._selected_button and child != self._selected_button:
                child.style = discord.ButtonStyle.grey
