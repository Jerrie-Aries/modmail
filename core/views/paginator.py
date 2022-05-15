from __future__ import annotations

from typing import Awaitable, Callable, List, Optional, Tuple, Union, TYPE_CHECKING

import discord
from discord import Embed
from discord.ui import Button, View

from core.enums_ext import PaginatorButtonItem

if TYPE_CHECKING:
    # these are for the sake of type hints only,
    # so no need to execute these in runtime
    from discord import Interaction, Message
    from discord.abc import Messageable
    from core.ext.commands import Context

__all__ = [
    "PaginatorSession",
    "EmbedPaginatorSession",
    "MessagePaginatorSession",
]

MISSING = discord.utils.MISSING


if TYPE_CHECKING:
    ButtonCallback = Callable[[Interaction], Awaitable]


class PaginatorButton(Button["PaginatorView"]):
    """
    Represents an instance of button component for Paginator.

    Parameters
    -----------
    item : PaginatorButtonItem
        The button item which contains `.label`, `.style` and `.emoji` attributes.
    item_callback : Callable[[Interaction], Any]
        The callback that should be called when an interaction is made. The value for this parameter
        should be a callable function/method which is defined without actually calling it.
    show_label : bool
        Whether the label name should be shown on the button's UI. Defaults to `True`.
    show_emoji : bool
        Whether the emoji should be shown on the button's UI. Defaults to `False`.
    """

    def __init__(
        self,
        item: PaginatorButtonItem,
        item_callback: ButtonCallback,
        show_label: bool = True,
        show_emoji: bool = False,
    ):
        if show_label and show_emoji:
            # if both are True, we will use the item name for the label
            label = item.name.title()
            emoji = item.emoji
        elif show_label:
            label = item.label
            emoji = None
        elif show_emoji:
            label = None
            emoji = item.emoji
        else:
            raise ValueError("'show_label' and 'show_emoji' cannot both be False.")

        super().__init__(label=label, emoji=emoji, style=item.style)

        self.button_item: PaginatorButtonItem = item
        self._button_callback: ButtonCallback = item_callback

    async def callback(self, interaction: Interaction) -> None:
        assert self.view is not None
        await self._button_callback(interaction)


class PaginatorView(View):
    """
    Paginator view. This class is used to implement the paginator buttons, arrange the button layouts
    and assign the button callback.

    Parameters
    -----------
    session : PaginatorSession
        The paginator session that implements this view.
    timeout : float
        Time before this view timed out. Defaults to `180` seconds.
    """

    children: List[PaginatorButton]

    def __init__(
        self,
        session: PaginatorSession,
        timeout: float = 180.0,
    ):
        super().__init__(timeout=timeout)

        self.session: PaginatorSession = session
        self.force_stop: bool = False

        self.add_buttons()

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user and self.session.author.id == interaction.user.id:
            return True
        await interaction.response.send_message(
            "This pagination menu cannot be controlled by you!", ephemeral=True
        )
        return False

    def add_buttons(self) -> None:
        """
        Adds all paginator buttons to this view.
        """
        for item in PaginatorButtonItem:
            # this probably would be faster instead of creating a map of all buttons with their callbacks
            # and do `.get()`
            item_callback = getattr(self, "_action_" + item.name.lower())
            self.add_item(PaginatorButton(item, item_callback))

        self.update_view()

    def update_view(self) -> None:
        """
        Updates the view of the buttons. This will be executed when instantiating this class and
        everytime interaction is made on this view.

        Depends on the value of `.session.current`, some buttons may be disabled.
        """
        is_first = self.session.current == 0
        is_last = self.session.current == len(self.session.pages) - 1

        for child in self.children:
            if child.button_item in (
                PaginatorButtonItem.FIRST,
                PaginatorButtonItem.BACK,
            ):
                child.disabled = is_first
            elif child.button_item in (
                PaginatorButtonItem.LAST,
                PaginatorButtonItem.NEXT,
            ):
                child.disabled = is_last
            else:
                child.disabled = False

    async def _action_first(self, interaction: Interaction) -> None:
        """
        Go to the first page.
        """
        content, embed = self.session.get_page(0)
        self.update_view()
        await interaction.response.edit_message(content=content, embed=embed, view=self)

    async def _action_back(self, interaction: Interaction) -> None:
        """
        Go to the previous page.
        """
        content, embed = self.session.get_page(self.session.current - 1)
        self.update_view()
        await interaction.response.edit_message(content=content, embed=embed, view=self)

    async def _action_next(self, interaction: Interaction) -> None:
        """
        Go to the next page.
        """
        content, embed = self.session.get_page(self.session.current + 1)
        self.update_view()
        await interaction.response.edit_message(content=content, embed=embed, view=self)

    async def _action_last(self, interaction: Interaction) -> None:
        """
        Go to the last page.
        """
        content, embed = self.session.get_page(len(self.session.pages) - 1)
        self.update_view()
        await interaction.response.edit_message(content=content, embed=embed, view=self)

    async def _action_close(self, interaction: Interaction) -> None:
        """
        Stops this view, closes the paginator session and deletes the base message.
        """
        self.force_stop = True
        await interaction.response.defer()
        self.stop()
        await interaction.message.delete()
        await self.session.close()

    async def on_timeout(self) -> None:
        self.disable_and_stop()
        await self.session.base.edit(view=self)

    def disable_all(self):
        """
        Manually disable buttons on the view.
        """
        for child in self.children:
            child.disabled = True

    def disable_and_stop(self) -> None:
        """
        Method to disable buttons and stop the view.
        """
        self.disable_all()
        if not self.is_finished():
            self.stop()


if TYPE_CHECKING:
    PagePayload = Tuple[str, Optional[Embed]]


class PaginatorSession:
    """
    Class that interactively paginates something.

    This class cannot be instantiated directly, instead use one of its subclasses.

    Parameters
    ----------
    ctx : Context
        The context of the command.
    pages : Tuple[Union[Embed, str]]
        A list of entries to paginate.
    timeout : float
        How long to wait for before the session closes.

    Attributes
    ----------
    ctx : Context
        The context of the command.
    timeout : float
        How long to wait for before the session closes.
    pages : List[Union[Embed, str]]
        A list of entries to paginate.
    running : bool
        Whether the paginate session is running.
    base : Message
        The `Message` of the `Embed`.
    current : int
        The current page number.
    destination : Messageable
        The `discord.abc.Messageable` channel where this paginator will be sent.
    view : Optional[PaginatorView]
        The `discord.View` attached to this paginator. If the length of pages is 1, this will be
        `None`.
    """

    def __init__(self, ctx: Context, *pages: Union[Embed, str], **options):
        self.ctx = ctx
        self.author = ctx.author
        self.timeout: int = options.get("timeout", 180)
        self.running: bool = False
        self.base: Message = MISSING  # implemented in `_create_base()` from subclass
        self.current: int = options.get("current", 0)
        self.pages: List[Union[Embed, str]] = list(pages)
        self.destination: Messageable = options.get("destination", ctx)

        self.view: Optional[PaginatorView] = (
            PaginatorView(session=self, timeout=self.timeout)
            if len(self.pages) > 1
            else MISSING
        )

    def add_page(self, item: Union[Embed, str]) -> None:
        """
        Add a page to this pagination. This should be overriden by subclass.
        """
        raise NotImplementedError

    async def create_base(self, item: Union[Embed, str]) -> None:
        """
        Create a base `Message`.
        """
        await self._create_base(item)

        if len(self.pages) == 1:
            self.running = False
            return

        self.running = True

    async def _create_base(self, item: Union[Embed, str]) -> None:
        raise NotImplementedError

    def get_page(self, index: int) -> PagePayload:
        """
        Get the page payload. This will call the method from subclass to retrieve
        the values for `content` and `embed` parameters in `Message.edit()`.

        Parameters
        ----------
        index : int
            The index of the page.
        """
        if not self.running:
            raise RuntimeError("This paginator session is not running.")

        page = self.pages[index]
        self.current = index

        content, embed = self._get_page(page)
        return content, embed

    def _get_page(self, page: Union[Embed, str]) -> PagePayload:
        raise NotImplementedError

    async def run(self) -> None:
        """
        Starts the paginator session.

        Returns
        -------
        Optional[Message]
            If it's closed before running ends.
        """
        if self.running:
            raise RuntimeError("This paginator session is already running.")

        await self.create_base(self.pages[self.current])

        if self.view is MISSING or not self.running:
            return

        await self.view.wait()

    async def close(self) -> None:
        """
        Closes the pagination session.

        Returns
        -------
        Optional[Message]
            If `delete` is `True`.
        """
        self.running = False

        sent_emoji, _ = await self.ctx.bot.retrieve_emoji()
        await self.ctx.bot.add_reaction(self.ctx.message, sent_emoji)


class EmbedPaginatorSession(PaginatorSession):
    def __init__(self, ctx: Context, *embeds: Embed, **options):
        super().__init__(ctx, *embeds, **options)

        if len(self.pages) > 1:
            for i, embed in enumerate(self.pages):
                footer_text = f"Page {i + 1} of {len(self.pages)}"
                if embed.footer.text:
                    footer_text = footer_text + " • " + embed.footer.text
                embed.set_footer(text=footer_text, icon_url=embed.footer.icon_url)

    def add_page(self, item: Embed) -> None:
        if not isinstance(item, Embed):
            raise TypeError("Page must be an Embed object.")

        self.pages.append(item)

    async def _create_base(self, item: Embed) -> None:
        self.base = await self.destination.send(embed=item, view=self.view)

    def _get_page(self, page: Embed) -> Tuple[MISSING, Embed]:
        return MISSING, page


class MessagePaginatorSession(PaginatorSession):
    def __init__(self, ctx: Context, *pages: str, embed: Embed = None, **options):
        super().__init__(ctx, *pages, **options)

        self.embed = embed
        if self.embed is None and len(pages) > 1:
            self.embed = self._default_embed()
        self.footer_text = self.embed.footer.text if self.embed is not None else None

    def add_page(self, item: str) -> None:
        if not isinstance(item, str):
            raise TypeError("Page must be a str object.")

        self.pages.append(item)

    def _default_embed(self) -> Embed:
        embed = Embed(color=self.ctx.bot.main_color)
        embed.set_footer(text="Paginator - Navigate using the buttons below.")
        return embed

    def _set_footer(self):
        if self.embed is not None:
            footer_text = f"Page {self.current + 1} of {len(self.pages)}"
            if self.footer_text:
                footer_text = footer_text + " • " + self.footer_text
            self.embed.set_footer(text=footer_text, icon_url=self.embed.footer.icon_url)

    async def _create_base(self, item: str) -> None:
        self._set_footer()
        self.base = await self.destination.send(
            content=item, embed=self.embed, view=self.view
        )

    def _get_page(self, page: str) -> Tuple[str, Optional[Embed]]:
        self._set_footer()
        return page, self.embed
