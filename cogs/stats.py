from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from cogs.resources.allroles import AllRolesResource
from cogs.resources.bot import BotResource
from cogs.resources.guild import GuildResource
from cogs.resources.member import MemberResource
from cogs.resources.role import RoleResource
from cogs.resources.role_members import RoleMembersResource
from core import checks
from core.ext import commands
from core.enums_ext import PermissionLevel
from core.views.paginator import EmbedPaginatorSession

if TYPE_CHECKING:
    from bot import ModmailBot


class Stats(commands.Cog):
    """Get useful stats about a member, the Modmail bot or your server."""

    def __init__(self, bot: ModmailBot):
        """
        Parameters
        ----------
        bot : ModmailBot
            The Modmail bot.
        """
        self.bot: ModmailBot = bot

    # Avatar

    @commands.command(aliases=["pfp"])
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def avatar(self, ctx: commands.Context, *, member: discord.Member = None):
        """
        Shows the avatar (profile picture) of a member.

        `member` if specified, may be a user ID, mention, or name.
        """
        if member is None:
            member = ctx.author
        embed = MemberResource(ctx, member).avatar_embed()
        await ctx.send(embed=embed)

    # Bot

    @commands.command()
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def botinfo(self, ctx: commands.Context):
        """
        Shows the stats of this Bot.
        """
        embed = BotResource(ctx, self.bot).bot_embed()
        await ctx.send(embed=embed)

    # Member

    @commands.hybrid_command(aliases=["whois", "memberinfo"])
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def userinfo(self, ctx: commands.Context, *, member: discord.Member = None):
        """
        Shows the stats of a member.

        `member` if specified, may be a user ID, mention, or name.
        """
        if member is None:
            member = ctx.author
        embed = MemberResource(ctx, member).member_embed()
        await ctx.send(embed=embed)

    # Status

    @commands.command(aliases=["us"])
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def userstatus(self, ctx: commands.Context, *, member: discord.Member = None):
        """
        Shows the status of a member.

        `member` if specified, may be a user ID, mention, or name.
        """
        if member is None:
            member = ctx.author
        embed = MemberResource(ctx, member).userstatus_embed()
        await ctx.send(embed=embed)

    # Role Members / In Role

    @commands.group(aliases=["ir", "rolemembers"], invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def inrole(self, ctx: commands.Context, *, role: discord.Role):
        """
        Get a list of members in a specified role.

        `role` may be a role ID, mention, or name.
        """
        embeds = RoleMembersResource(ctx, role).role_members_embed()

        session = EmbedPaginatorSession(ctx, *embeds)
        await session.run()

    @inrole.command()
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def mention(self, ctx: commands.Context, *, role: discord.Role):
        """
        Shows the `inrole`'s member list in mention.

        `role` may be a ID, mention, or name.
        """
        embeds = RoleMembersResource(
            ctx, role, member_mention=True
        ).role_members_embed()

        session = EmbedPaginatorSession(ctx, *embeds)
        await session.run()

    # Role

    @commands.command()
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def roleinfo(self, ctx: commands.Context, *, role: discord.Role):
        """
        Shows the stats of a role.

        `role` may be a ID, mention, or name.
        """
        embed = RoleResource(ctx, role).role_embed()
        await ctx.send(embed=embed)

    # Allroles

    @commands.command()
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def allroles(self, ctx: commands.Context):
        """
        Get the list of roles on this server.
        """
        embeds = AllRolesResource(ctx).all_roles_embed()

        session = EmbedPaginatorSession(ctx, *embeds)
        await session.run()

    # Server

    @commands.command(aliases=["guildinfo"])
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def serverinfo(self, ctx: commands.Context):
        """
        Shows the stats of this server.
        """
        embed = GuildResource(ctx).guild_embed()
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Stats(bot))
