from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from core.timeutils import datetime_formatter

if TYPE_CHECKING:
    from discord import Role

    from core.ext.commands import Context


class RoleResource:
    def __init__(self, ctx: Context, role: Role):
        self.ctx: Context = ctx
        self.role: Role = role

    def role_embed(self) -> discord.Embed:
        """Create an embed containing the role's information."""

        r: discord.Role = self.role

        rolecolor = str(r.color).upper()

        embed = discord.Embed(color=r.color)

        embed.set_author(name=f"{r.name}")

        embed.add_field(name="Role Name:", value=f"{r.name}")
        embed.add_field(name="Color:", value=rolecolor)
        embed.add_field(name="Members:", value=f"{len(r.members)}")
        embed.add_field(
            name="Created at:", value=datetime_formatter.time_age(r.created_at)
        )
        embed.add_field(name="Role Position:", value=r.position)
        embed.add_field(name="Mention:", value=r.mention)
        embed.add_field(name="Hoisted:", value=r.hoist)
        embed.add_field(name="Mentionable:", value=r.mentionable)
        embed.add_field(name="Managed:", value=r.managed)

        embed.set_thumbnail(url=f"https://placehold.it/100/{str(rolecolor)[1:]}?text=+")
        embed.set_footer(text=f"Role ID: {r.id}")

        return embed
