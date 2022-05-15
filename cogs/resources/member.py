from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from core.timeutils import datetime_formatter

if TYPE_CHECKING:
    from discord import Member

    from core.ext.commands import Context


class MemberResource:
    def __init__(self, ctx: Context, member: Member):
        self.ctx: Context = ctx
        self.member: Member = member

    def member_embed(self) -> discord.Embed:
        """Create an embed containing the member's information."""

        # Find all of the roles a member has
        role_list = [
            role.mention
            for role in reversed(self.member.roles)
            if role is not self.ctx.guild.default_role
        ]

        join_position = (
            sorted(self.member.guild.members, key=lambda m: m.joined_at).index(
                self.member
            )
            + 1
        )

        embed = discord.Embed(color=self.member.color)

        embed.set_author(name=f"{str(self.member)}")

        embed.add_field(
            name="Created:", value=datetime_formatter.time_age(self.member.created_at)
        )
        embed.add_field(
            name="Joined:", value=datetime_formatter.time_age(self.member.joined_at)
        )
        embed.add_field(name="Join Position:", value=f"{join_position}")
        embed.add_field(
            name="Avatar URL:", value=f"[Link]({self.member.display_avatar.url})"
        )
        embed.add_field(name="Mention:", value=self.member.mention)

        if self.member.activity is not None:
            activitytype = self.member.activity.type.name.title()  # type: ignore
            activitytype += " to" if activitytype == "Listening" else ""

            embed.add_field(
                name="Activity:", value=f"{activitytype} {self.member.activity.name}"
            )

        embed.add_field(name="Status:", value=self.member.status.name.title())  # type: ignore
        embed.add_field(name="Nickname:", value=self.member.nick)
        embed.add_field(
            name="Roles:", value=" ".join(role_list) if role_list else "None."
        )

        embed.set_thumbnail(url=self.member.display_avatar.url)
        embed.set_footer(text=f"User ID: {self.member.id}")

        return embed

    def avatar_embed(self) -> discord.Embed:
        """Create an embed contain the member's avatar."""

        embed = discord.Embed(color=self.member.color)

        embed.set_author(name=f"{str(self.member)}'s Avatar")

        embed.add_field(
            name="Avatar", value=f"[Link]({self.member.display_avatar.url})"
        )

        embed.set_image(url=self.member.display_avatar.url)
        embed.set_footer(text=f"User ID: {self.member.id}")

        return embed

    def userstatus_embed(self) -> discord.Embed:
        """Create an embed that shows the status of a member"""

        statuses = ["online", "idle", "dnd", "offline"]
        colors = ["0x7ccca5", "0xfca41b", "0xf44444", "0x9da4ad"]
        statuscolour = colors[statuses.index(self.member.status.name)]  # type: ignore
        embed = discord.Embed(color=discord.Color(int(statuscolour, 0)))
        images = [
            "https://cdn.discordapp.com/emojis/615846944341884948.png?v=1%27",
            "https://cdn.discordapp.com/emojis/587932578221129748.png?v=1%27%22",
            "https://cdn.discordapp.com/emojis/500353506474065920.png?v=1%27%22",
            "https://cdn.discordapp.com/emojis/606534231492919312.png?v=1%27%22",
        ]
        embed.set_image(url=images[statuses.index(self.member.status.name)])  # type: ignore
        embed.set_author(name=f"{str(self.member)}'s Status")
        embed.add_field(name="Status", value=self.member.status.name.title())  # type: ignore
        embed.set_footer(text=f"User ID: {self.member.id}")

        return embed
