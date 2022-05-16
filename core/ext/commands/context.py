from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import (
    Optional,
    TypeVar,
    TYPE_CHECKING,
    Union,
)

import discord

from core.ext.commands import DPYContext

if TYPE_CHECKING:
    from bot import ModmailBot
    from core.thread import ModmailThread

    BotT = TypeVar("BotT", bound=ModmailBot)
else:
    BotT = TypeVar("BotT")


class Context(DPYContext[BotT]):
    """
    Represents the custom context in which a command is being invoked under.

    This class contains a lot of meta data to help you understand more about
    the invocation context.

    This class is not created manually and is instead passed around to commands
    as the first parameter.
    """

    bot: BotT

    def __init__(self, **attrs):
        super().__init__(**attrs)

        self.custom_attrs = SimpleNamespace()

    @property
    def modmail_thread(self) -> Optional[ModmailThread]:
        """
        Returns a :class:`ModmailThread` instance if the current context within Modmail thread channel.
        Otherwise returns `None`.

        Returns
        --------
        thread : Optional[ModmailThread]
            The ModmailThread instance of this context, or None.
        """
        return getattr(self.custom_attrs, "modmail_thread", None)

    @modmail_thread.setter
    def modmail_thread(self, item: Optional[ModmailThread]) -> None:
        """
        Sets the `modmail_thread` attribute for current context.

        This will be automatically implemented in method `get_context` or `create_context`.

        Parameters
        -----------
        item : Optional[ModmailThread]
            The ModmailThread instance, or None.
        """
        self.custom_attrs.modmail_thread = item
