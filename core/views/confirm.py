from __future__ import annotations

from typing import List, Optional, Union, TYPE_CHECKING

import discord
from discord import ButtonStyle, Interaction
from discord.ui import Button, View

if TYPE_CHECKING:
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
        super().__init__(label=item["label"], emoji=None, style=item["style"])

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
    user : Union[discord.Member, discord.User]
        The author that triggered this confirmation view.
    timeout : float
        Time before this view timed out. Defaults to `20` seconds.
    """

    children: List[ConfirmationButton]

    def __init__(
        self,
        user: Union[discord.Member, discord.User],
        timeout: float = 20.0,
    ):
        self.user: Union[discord.Member, discord.User] = user
        super().__init__(timeout=timeout)

        # with bot parameter, we can insert custom emojis from config here
        self.button_map: List[ConfirmationButtonItem] = [
            {
                "label": "Yes",
                "style": ButtonStyle.green,
                "callback": self._action_confirm,
            },
            {
                "label": "No",
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

        # still need to do this check to make sure we set the correct Message
        # in this case we just match the `custom_id` of one of the components
        matched = False
        for comp in item.components:
            comp_children = getattr(comp, "children", [])
            for child in comp_children:
                child_id = getattr(child, "custom_id", None)
                match = discord.utils.find(
                    lambda x: child_id and x.custom_id == child_id, self.children
                )
                if match is not None:
                    matched = True
                    break
            if matched:
                break
        else:
            raise ValueError("Invalid item. No matched IDs from child components.")

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
