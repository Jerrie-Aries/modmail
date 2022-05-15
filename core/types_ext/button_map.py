from __future__ import annotations

from typing import Awaitable, Callable, TypedDict, TYPE_CHECKING

from discord import ButtonStyle, Interaction


if TYPE_CHECKING:
    # to prevent possible circular import
    from core.views.confirm import ConfirmationButton

    ConfirmationButtonCallback = Callable[[ConfirmationButton, Interaction], Awaitable]


class ConfirmationButtonItem(TypedDict):
    label: str
    style: ButtonStyle
    callback: ConfirmationButtonCallback
