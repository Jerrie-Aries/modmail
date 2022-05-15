from __future__ import annotations

from typing import TypedDict, List, Optional

from discord.types.snowflake import Snowflake, SnowflakeList


class MongoRawData(TypedDict):
    _id: str


class UserPayload(TypedDict):
    id: Snowflake
    name: str
    discriminator: str
    avatar_url: str
    mod: bool


class AttachmentPayload(TypedDict):
    id: Snowflake
    filename: str
    is_image: bool
    size: int
    url: str


class PostLogPayload(TypedDict):
    open: bool
    closed_at: str
    nsfw: Optional[bool]
    close_message: Optional[str]
    closer: UserPayload


class AppendLogPayload(TypedDict):
    timestamp: str
    message_id: Snowflake
    linked_ids: SnowflakeList
    content: str
    author: UserPayload
    type: str
    attachments: List[AttachmentPayload]


class PersistentNoteAuthorPayload(TypedDict):
    id: Snowflake
    name: str
    discriminator: str
    avatar_url: str


class PersistentNotePayload(MongoRawData):
    recipient: str
    author: PersistentNoteAuthorPayload
    message: str
    message_id: Snowflake


class ThreadMessagePayload(TypedDict):
    timestamp: str
    message_id: Snowflake
    linked_ids: List[str]
    author: UserPayload
    content: str
    type: str
    attachments: List[AttachmentPayload]


class ThreadMessageLogPayload(MongoRawData):
    messages: List[ThreadMessagePayload]


class ThreadLogPayload(MongoRawData):
    key: str
    open: bool
    created_at: str
    closed_at: Optional[str]
    channel_id: Snowflake
    guild_id: Snowflake
    bot_id: Snowflake
    recipient: UserPayload
    creator: UserPayload
    closer: Optional[UserPayload]
    messages: List[ThreadMessagePayload]
    close_message: Optional[str]
    nsfw: Optional[bool]
