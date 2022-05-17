from __future__ import annotations

import asyncio
from difflib import get_close_matches
from typing import Any, Dict, Optional, Union, TYPE_CHECKING

import discord

from core import checks
from core.enums_ext import PermissionLevel
from core.ext import commands
from core.timeutils import datetime_formatter
from core.utils import code_block, escape_code_block, plural, trigger_typing
from core.views.paginator import EmbedPaginatorSession

if TYPE_CHECKING:
    from pymongo.collection import Collection
    from bot import ModmailBot


MISSING = discord.utils.MISSING


@checks.has_permissions(PermissionLevel.OWNER)
async def debug_pastebin(ctx: commands.Context):
    """
    Posts application-logs to Pastebin.
    """
    cog = ctx.bot.get_cog("Developer")
    if not ctx.bot.log_file_path.exists():
        raise commands.BadArgument("Log file does not exist.")

    dev_key = cog.secret_keys.get("pastebin_api_dev")
    if not dev_key:
        raise commands.BadArgument("The Pastebin Developer key is not set.")

    paste_url = "https://pastebin.com/api/api_post.php"
    title = "Application Logs"

    data = {
        "api_dev_key": dev_key,
        "api_option": "paste",
        "api_paste_name": title,
        "api_paste_expire_date": "10M",
    }

    with open(ctx.bot.log_file_path, "r+", encoding="utf-8") as f:
        logs = f.read().strip()
        data["api_paste_code"] = logs
        data["api_paste_format"] = "haskell"

    async with ctx.bot.session.post(paste_url, data=data) as resp:
        raw = await resp.text()

    embed = discord.Embed(
        title="Debug Logs",
        color=ctx.bot.main_color,
        description=f"Open in browser: {raw}",
    )
    await ctx.send(embed=embed)


class Developer(commands.Cog):
    """
    Commands specifically for bot developers.
    """

    doc_id = "dev_config"
    default_config: Dict[str, Any] = {
        "secret_keys": {},
    }

    def __init__(self, bot: ModmailBot):
        self.bot: ModmailBot = bot
        self.db: Collection = bot.api.get_plugin_partition(self)
        self.ready_event: asyncio.Event = asyncio.Event()

        self._config_cache: Dict[str, Any] = {}

    async def cog_load(self):
        asyncio.create_task(self.add_debug_pastebin())

    async def cog_unload(self):
        debug_cmd = self.bot.get_command("debug")
        if isinstance(debug_cmd, commands.Group):
            debug_cmd.remove_command("pastebin")

        self.ready_event.clear()

    async def populate_config(self) -> None:
        """
        Populate and validate config.
        """
        await self.bot.wait_for_connected()

        config = self.db.find_one({"_id": self.doc_id})
        if config is None:
            config = await self.db.find_one_and_update(
                {"_id": self.doc_id},
                {"$set": self.default_config},
                upsert=True,
                return_document=True,
            )
        self.refresh_config(data=config)

        self.ready_event.set()

    async def update_db(self, data: Dict[str, Any]) -> None:
        """
        Update the database and refresh the config cache.
        """
        config = await self.db.find_one_and_update(
            {"_id": self.doc_id},
            {"$set": data},
            upsert=True,
            return_document=True,
        )

        self.refresh_config(data=config)

    def refresh_config(self, data: Dict[str, Any]) -> None:
        for k, v in data.items():
            if k == "_id":
                continue
            self.config[k] = v

    @property
    def config(self) -> Dict[str, Any]:
        return self._config_cache

    @property
    def secret_keys(self) -> Dict[str, str]:
        return self.config.get("secret_keys", {})

    @commands.group(invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.OWNER)
    async def api(self, ctx: commands.Context):
        """
        API related commands.
        """
        await ctx.send_help(ctx.command)

    @api.command(name="setkey")
    @checks.has_permissions(PermissionLevel.OWNER)
    async def api_setkey(self, ctx: commands.Context, name: str.lower, *, key: str):
        """
        Set API or secret keys that are required for Clash account linking, Co-Op clans registry, Staff Application commands, etc.

        **Available options for parameter `name`:**
        - `pastebin_api_dev`

        **Examples:**
        - `{prefix}api setkey pastebin_api_dev <key>`
        """
        secret_keys: Dict[str, str] = self.secret_keys
        valid_keys = ("pastebin_api_dev",)
        if name not in valid_keys:
            raise commands.BadArgument("Invalid API key name provided.")

        secret_keys.update({name: key})
        await self.update_db(data={"secret_keys": secret_keys})

        embed = discord.Embed(
            title="Success",
            color=self.bot.main_color,
            description=f"`{name}` key is now set to `{key}`.",
        )
        await ctx.send(embed=embed)

    async def add_debug_pastebin(self) -> None:
        """
        Adds a `pastebin` subcommand for `debug` command.
        """
        await self.bot.wait_until_ready()
        debug_cmd = self.bot.get_command("debug")
        if isinstance(debug_cmd, commands.Group):
            pastebin_cmd = commands.Command(
                debug_pastebin, name="pastebin", aliases=["paste"]
            )
            debug_cmd.add_command(pastebin_cmd)

    @commands.command()
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def inname(self, ctx: commands.Context, *, name: str):
        """
        Search guild members by name.
        """

        def get_match(member):
            name_list = [member.name.lower()]
            if member.nick:
                name_list.append(member.nick.lower())
            return get_close_matches(name.lower(), name_list, cutoff=0.7)

        member_list = list(
            filter(
                lambda m: name.lower() in m.name.lower()
                or (m.nick and name.lower() in str(m.nick.lower()))
                or get_match(m),  # if empty list is returned, this will be False
                ctx.guild.members,
            )
        )

        def base_embed(continued=False, description=None):
            embed = discord.Embed(color=self.bot.main_color)
            embed.description = description if description is not None else ""
            embed.title = f'Search results for "{name}".'
            if continued:
                embed.title += " (Continued)"
            return embed

        embeds = [
            base_embed(
                description=f"**Found {plural(len(member_list)):member}.**\n\n"
                if member_list
                else "**Not found.\n\n**",
            )
        ]
        entries = 0
        embed = embeds[0]
        num = 1

        if member_list:
            if len(member_list) > 100:
                embed.description += "*The list is too long in length. Narrow down the search results by providing more specific name.*"

            else:
                for m in member_list:
                    desc = (
                        f"**{num}.** {m.mention} - {m.name + '#' + m.discriminator}\n"
                        f"Nickname: {m.nick if m.nick else '*None*'}\n"
                        f"ID: {m.id}\n\n"
                    )

                    if entries == 15:
                        embed = base_embed(True, desc)
                        embeds.append(embed)
                        entries = 1
                        num += 1
                    else:
                        embed.description += desc
                        entries += 1
                        num += 1

        session = EmbedPaginatorSession(ctx, *embeds)
        await session.run()

    @commands.group(invoke_without_command=True, usage="[option]")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def fetch(self, ctx: commands.Context):
        """
        Fetch commands.
        """
        await ctx.send_help(ctx.command)

    @fetch.command(name="user")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    @trigger_typing
    async def fetch_user(self, ctx: commands.Context, user: Union[discord.User, int]):
        """
        Fetch basic info of any Discord user by ID.
        """
        if not isinstance(user, discord.User):
            if isinstance(user, int):
                try:
                    user = await self.bot.fetch_user(user)
                except (discord.NotFound, Exception):
                    raise commands.BadArgument('User "{}" not found.'.format(user))
            else:
                raise commands.BadArgument(
                    'User "{}" not found. Argument was not `int`.'.format(user)
                )
        user: discord.User = user

        embed = discord.Embed(color=self.bot.main_color)
        embed.set_author(name=str(user))
        embed.add_field(
            name="Created:", value=datetime_formatter.format_dt(user.created_at)
        )
        embed.add_field(
            name="Account age:", value=datetime_formatter.age(user.created_at)
        )
        embed.add_field(name="Avatar URL:", value=f"[Link]({user.display_avatar.url})")
        embed.add_field(name="Mention:", value=user.mention)

        mutual_guilds = [
            g
            for g in self.bot.guilds
            if user.id not in self.bot.bot_owner_ids
            if user in g.members
        ]
        if mutual_guilds:
            embed.add_field(
                name="Mutual Server(s):", value=", ".join(g.name for g in mutual_guilds)
            )

        embed.set_thumbnail(url=user.display_avatar.url)
        embed.set_footer(text=f"User ID: {user.id}")

        await ctx.send(embed=embed)

    @fetch.command(name="message")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    @trigger_typing
    async def fetch_message(
        self, ctx: commands.Context, message: discord.Message, *options
    ):
        """
        Fetch raw message content and post them in embed or plain text.

        `message` may be a message ID if the message is in the same channel where the command is being invoked.
        Otherwise, use the format `channel_id-message_id` or message URL instead.

        The lookup strategy is as follows (in order):
        1. Lookup by "`channel_id-message_id`" (retrieved by shift-clicking on "Copy ID")
        2. Lookup by message ID (the message **must** be in the context channel)
        3. Lookup by message URL

        **The following options are valid:**
        `--embed`: Post the content in embed. By default, the bot will post the content in plain message.
        `--escape-codeblock` or `--esc-cb`: Returns the text with code block escaped.
        `--wrap-codeblock` or `--wrap-cb` : Returns the text wrapped in code block.

        __**Note:**__
        - This command can only be used to retrieve the content of plain messages.
        If you want get the raw or contents of embeds, consider using the Embed Manager plugin instead.
        """
        emb_opt = ["--embed"]
        esc_cb_opt = ["--escape-codeblock", "--esc-cb"]
        wrap_cb_opt = ["--wrap-codeblock", "--wrap-cb"]
        in_embed = False
        esc_codeblock = False
        wrap = False
        for op in options:
            if op in emb_opt:
                in_embed = True
            elif op in esc_cb_opt:
                esc_codeblock = True
            elif op in wrap_cb_opt:
                wrap = True
            else:
                raise commands.BadArgument(
                    f'"{op}" is not a valid option for parameter `options`.'
                )

        content = message.content
        if esc_codeblock:
            content = escape_code_block(content)

        if wrap:
            content = code_block(content)

        author = message.author
        embed = discord.Embed(color=self.bot.main_color, timestamp=message.created_at)
        embed.set_footer(text=f"Message ID: {message.id}")
        embed.set_author(name=str(author), icon_url=author.display_avatar.url)
        embed.add_field(name="Link:", value=f"[Jump to message!]({message.jump_url})")
        if in_embed:
            embed.description = content
            await ctx.send(embed=embed)
        else:
            await ctx.send(content, embed=embed)

    async def _clone_channel(
        self, channel: discord.TextChannel, category: Optional[discord.CategoryChannel]
    ) -> discord.TextChannel:
        """
        An internal method to clone the channel and all of its contents.
        The messages inside this channel will be sent to the cloned channel using a webhook.

        Parameters
        -----------
        channel: discord.TextChannel
            The text channel to clone.
        category : Optional[discord.CategoryChannel]
            The category where the cloned channel will be created. If `None` is passed, fallbacks to
            `TextChannel.category` which the value could be `None` as well. If `None`, the channel will
            be created outside of category.
        """
        # clone the channel
        if category is None:
            category = channel.category
            if category is None:
                overwrites = {
                    channel.guild.default_role: discord.PermissionOverwrite(
                        read_messages=False
                    )
                }
            else:
                overwrites = MISSING
            clone_channel = await channel.guild.create_text_channel(
                channel.name,
                category=category,
                overwrites=overwrites,
                reason="Cloning channel.",
            )
        else:
            clone_channel = await category.create_text_channel(
                channel.name, overwrites=MISSING, reason="Cloning channel."
            )
        overwrite = clone_channel.overwrites_for(clone_channel.guild.default_role)
        if not overwrite.use_external_emojis:
            overwrite.use_external_emojis = True  # noqa
            await clone_channel.set_permissions(
                clone_channel.guild.default_role,
                overwrite=overwrite,
                reason="To be able to send custom emojis with Webhook.",
            )

        # create a new webhook
        avatar = await self.bot.user.display_avatar.read()
        wh = await clone_channel.create_webhook(name=self.bot.user.name, avatar=avatar)

        # copy messages from original channel
        async for message in channel.history(limit=100, oldest_first=True):
            if not message.type == discord.MessageType.default:
                continue
            author = message.author
            embed = MISSING
            if message.embeds:
                embed = message.embeds[0]
            content = MISSING
            if message.content:
                content = discord.utils.escape_mentions(message.content)
            files = []
            if message.attachments:
                for attch in message.attachments:
                    file = await attch.to_file()
                    files.append(file)
            if not files:
                files = MISSING
            if not embed and not content and not files:
                continue
            await wh.send(
                username=f"{author}",
                avatar_url=author.display_avatar.url,
                content=content,
                embed=embed,
                files=files,
                wait=True,
            )

            # rate limit is 30 / minute / channel
            # so we can send only one every 2 seconds
            await asyncio.sleep(2)

        return clone_channel

    @commands.group(usage="[option]", invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def archive(self, ctx: commands.Context):
        """
        Archive a category or channel and all of its contents.

        Archiving a category or channel from one server to another is also supported.
        This is basically cloning the category or channel and all of its contents.
        The messages in the channels will be sent to the cloned ones using a webhook.

        __**Note:**__
        - In order to execute any of these operations, the bot must have `Administrator` permission.
        - These operations can be slow due to rate limits from Discord.
        """
        await ctx.send_help(ctx.command)

    @archive.command(name="channel")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def archive_channel(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
        category: Union[discord.CategoryChannel, int] = None,
    ):
        """
        Archive a text channel.

        `channel` may be a channel ID, mention, or name.

        `category` is the category where you want the cloned channel to be created.
        If specified may be a category ID, mention, or name, if the category is from the guild where the command is ran. Otherwise the category ID must be provided instead.
        And if `category` is not specified, the cloned channel will be created inside the category where the original channel belongs to, if any, otherwise, outside i.e. without category.

        __**Note:**__
        - The bot must have `Administrator` permission to execute this operation.
        """
        if not isinstance(category, discord.CategoryChannel):
            # this will get the category channel whether from inside or outside of this ctx.guild
            if category is not None:
                # category is `int` type
                category_id = category
                category = self.bot.get_channel(category_id)
                if not isinstance(category, discord.CategoryChannel):
                    # whether `None` or the type is not `discord.CategoryChannel`, just raise
                    # since the ID was provided and turned out to be invalid
                    raise commands.BadArgument(f'Category "{category_id}" not found.')

        # still need to do permission checks as well before proceeding
        if not ctx.me.guild_permissions.administrator:
            raise commands.BadArgument(
                f"I do not have Administrator permission in this server to execute this operation."
            )
        if category and category.guild != ctx.guild:
            guild_me = category.guild.get_member(self.bot.user.id)
            if not guild_me.guild_permissions.administrator:
                raise commands.BadArgument(
                    f"I do not have Administrator permission in {category.guild.name} server to execute this operation."
                )

        embed = discord.Embed(
            description=f"Archiving `{channel.name}` channel. This may take a moment...",
            color=self.bot.main_color,
        )

        msg = await ctx.send(embed=embed)

        async with ctx.typing():
            # category could be `None` if not specified in the command
            clone = await self._clone_channel(channel, category)

        embed.title = "Success"
        embed.description = f"Successfully archived `{channel.name}` channel."
        embed.add_field(name="Destination channel:", value=clone.mention)
        await msg.edit(embed=embed)

    @archive.command(name="category")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def archive_category(
        self,
        ctx: commands.Context,
        category: discord.CategoryChannel,
        guild: discord.Guild = None,
    ):
        """
        Archive a category and all of its text channels.

        `category` may be a category ID, mention, or name.
        `guild` is the server where you want the cloned category to be created, if specified may be a guild ID or name.
        If not specified, the cloned category will be created inside the guild where the command is being executed.

        __**Note:**__
        - The bot must have `Administrator` permission to execute this operation.
        """
        if guild is None:
            guild = ctx.guild

        if not ctx.me.guild_permissions.administrator:
            raise commands.BadArgument(
                f"I do not have Administrator permission in this server to execute this operation."
            )
        if guild != ctx.guild:
            guild_me = guild.get_member(self.bot.user.id)
            if not guild_me.guild_permissions.administrator:
                raise commands.BadArgument(
                    f"I do not have Administrator permission in {guild.name} server to execute this operation."
                )
        embed = discord.Embed(
            description=f"Archiving `{category.name}` category. This may take a moment...",
            color=self.bot.main_color,
        )

        msg = await ctx.send(embed=embed)

        async with ctx.typing():
            category_permissions = {
                guild.default_role: discord.PermissionOverwrite(
                    read_messages=True, use_external_emojis=True
                ),
            }
            clone_category = await guild.create_category(
                category.name,
                overwrites=category_permissions,
                reason="Cloning category.",
            )

            for channel in sorted(category.channels, key=lambda c: c.position):
                await self._clone_channel(channel, clone_category)

                await asyncio.sleep(1)

        embed.title = "Success"
        embed.description = f"Successfully archived `{category.name}` category."
        embed.add_field(name="Destination category:", value=clone_category.mention)
        await msg.edit(embed=embed)

    @commands.group(aliases=["ac"], invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.OWNER)
    async def app_command(self, ctx: commands.Context):
        """
        Application commands manager.
        """
        await ctx.send_help(ctx.command)

    @app_command.command(name="add")
    @checks.has_permissions(PermissionLevel.OWNER)
    async def ac_add(
        self, ctx: commands.Context, name: str, guild: Optional[discord.Guild] = None
    ):
        """
        Add a command to application commands.
        """
        await ctx.send("Not implemented.")

    @app_command.command(name="remove")
    @checks.has_permissions(PermissionLevel.OWNER)
    async def ac_remove(
        self, ctx: commands.Context, name: str, guild: Optional[discord.Guild] = None
    ):
        """
        Remove a command from application commands.
        """
        await ctx.send("Not implemented.")

    @app_command.command(name="clear")
    @checks.has_permissions(PermissionLevel.OWNER)
    async def ac_clear(
        self, ctx: commands.Context, name: str, guild: Optional[discord.Guild] = None
    ):
        """
        Clear all commands from application commands.
        """
        await ctx.send("Not implemented.")

    @app_command.command(name="sync")
    @checks.has_permissions(PermissionLevel.OWNER)
    async def ac_sync(
        self, ctx: commands.Context, guild: Optional[str] = None
    ):
        """
        Sync application commands for specified guild.
        
        For `guild` parameter, you may pass a guild ID, name or "global".
        If not passed, fallback to guild where the command is executed.
        """
        if guild is None:
            guild = ctx.guild
        elif guild.lower() == "global":
            guild = None
        else:
            conv = commands.GuildConverter
            argument = guild
            try:
                guild = await conv.convert(ctx, argument)
            except commands.GuildNotFound:
                raise commands.BadArgument(f'Guild "{argument}" not found.')

        guild_cmds = self.bot.tree.get_commands(guild=guild)
        for cmd in self.bot.tree.get_commands():
            if cmd not in guild_cmds:
                self.bot.tree.add_command(cmd, guild=guild)
        await self.bot.tree.sync(guild=guild)
        await ctx.send("Done.")


async def setup(bot):
    await bot.add_cog(Developer(bot))
