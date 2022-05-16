from __future__ import annotations

import secrets
import sys
from abc import abstractmethod, ABCMeta
from json import JSONDecodeError
from typing import Any, Dict, Union, Optional, List, TYPE_CHECKING

import discord
from aiohttp import ClientResponseError, ClientResponse
from discord import Member, DMChannel, TextChannel, Message
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import (
    ConfigurationError,
    OperationFailure,
    ServerSelectionTimeoutError,
)

from core.enums_ext import ThreadMessageType
from core.logging_ext import getLogger
from core.models import ThreadMessage, ThreadMessageLog

if TYPE_CHECKING:
    from aiohttp import ClientSession
    from pymongo import MongoClient
    from pymongo.collection import Collection
    from pymongo.database import Database

    from bot import ModmailBot
    from core.ext.commands import Cog
    from core.models import PartialPersistentNote
    from core.types_ext.raw_data import (
        AppendLogPayload,
        ThreadMessageLogPayload,
        PersistentNotePayload,
        PostLogPayload,
        ThreadLogPayload,
    )

    ThreadLogPayload = Optional[Union[ThreadLogPayload, List[Any], str, bool, int]]


logger = getLogger(__name__)


class ApiClient(metaclass=ABCMeta):
    """
    This class represents the general request class for all type of clients.

    Parameters
    ----------
    bot : ModmailBot
        The Modmail bot.
    db : Database
        The database this client connected to.

    Attributes
    ----------
    bot : ModmailBot
        The Modmail bot.
    session : ClientSession
        The session that will be used when making HTTP requests.
    github : GitHub
        The GitHub instance.
    """

    def __init__(self, bot: ModmailBot, db: Database):
        self.bot: ModmailBot = bot
        self.db: Database = db
        self.session: ClientSession = bot.session

        self.github: GitHub = GitHub(bot, access_token=bot.config.get("github_token"))

    async def request(
        self,
        url: str,
        method: str = "GET",
        headers: dict = None,
        payload: dict = None,
        return_response: bool = False,
    ) -> Union[ClientResponse, dict, list, str]:
        """
        Makes a HTTP request.

        Parameters
        ----------
        url : str
            The destination URL of the request.
        method : str
            The HTTP method (POST, GET, PUT, DELETE, FETCH, etc.).
        headers : Dict[str, str]
            Additional headers to `headers`.
        payload : Dict[str, Any]
            The json payload to be sent along the request.
        return_response : bool
            Whether the `ClientResponse` object should be returned.

        Returns
        -------
        :class:`ClientResponse` or :class:`dict` or :class:`list` or :class:`str`
            `ClientResponse` if `return_response` is `True`.
            `dict` if the returned data is a json object.
            `list` if the returned data is a json list.
            `str` if the returned data is not a valid json data, the raw response.
        """
        async with self.session.request(
            method, url, headers=headers, json=payload
        ) as resp:
            if return_response:
                return resp
            try:
                return await resp.json()
            except (JSONDecodeError, ClientResponseError):
                return await resp.text()

    @property
    def logs(self) -> Collection:
        """
        Returns the logs collection from the database.
        """
        raise NotImplementedError

    @abstractmethod
    async def setup_indexes(self) -> None:
        """Setup the database indexes."""
        raise NotImplementedError

    @abstractmethod
    async def validate_database_connection(self) -> None:
        """
        Validate the database connection. This will be ran `on_connect` to make sure
        the database is successfully connected and binded.
        """
        raise NotImplementedError

    @abstractmethod
    async def get_user_logs(self, user_id: Union[str, int]) -> List[ThreadLogPayload]:
        """
        List of logs of specified user.
        """
        raise NotImplementedError

    @abstractmethod
    async def get_latest_user_logs(self, user_id: Union[str, int]) -> ThreadLogPayload:
        """
        Get the latest log of specified user.
        """
        raise NotImplementedError

    @abstractmethod
    async def get_responded_logs(self, user_id: Union[str, int]) -> List[dict]:
        """
        List of logs that were responded by specified user.
        """
        raise NotImplementedError

    @abstractmethod
    async def get_open_logs(self) -> List[dict]:
        """
        List of logs that are currently open.
        """
        raise NotImplementedError

    @abstractmethod
    async def get_log(
        self, channel_id: Union[str, int], field: str = None
    ) -> ThreadLogPayload:
        """
        Get current thread log. `field` if specified, the value of it will be returned.
        """
        raise NotImplementedError

    @abstractmethod
    async def get_message_payload(
        self, message: Message, channel: TextChannel
    ) -> Optional[ThreadMessage]:
        """
        Get message payload. This will fetch single message payload logged in the database that matches
        with the provided message object.
        """
        raise NotImplementedError

    @abstractmethod
    async def get_message_log_payload(
        self, channel: TextChannel
    ) -> Optional[ThreadMessageLog]:
        """
        Get messsage log payload. Basically this will fetch all the messages logged in the database
        from the thread channel.
        """
        raise NotImplementedError

    @abstractmethod
    async def get_log_link(self, channel_id: Union[str, int]) -> str:
        """
        Get the log link of the current thread, or specified channel.
        """
        raise NotImplementedError

    @abstractmethod
    async def create_log_entry(
        self, recipient: Member, channel: TextChannel, creator: Member
    ) -> str:
        """
        Create a log entry of current thread.
        """
        raise NotImplementedError

    @abstractmethod
    async def delete_log_entry(self, key: str) -> bool:
        """
        Delete the log entry.
        """
        raise NotImplementedError

    @abstractmethod
    async def delete_all_logs(self) -> int:
        """
        Purge all logs.
        """
        raise NotImplementedError

    @abstractmethod
    async def get_config(self) -> dict:
        """
        Get config from the database.
        """
        raise NotImplementedError

    @abstractmethod
    async def update_config(self, data: dict) -> None:
        """
        Update config.
        """
        raise NotImplementedError

    @abstractmethod
    async def edit_message(self, message_id: Union[int, str], new_content: str) -> None:
        """
        Update the edited thread message in the database.
        """
        raise NotImplementedError

    @abstractmethod
    async def append_log(
        self,
        message: Message,
        *,
        message_id: str = "",
        channel_id: str = "",
        type_: ThreadMessageType = ThreadMessageType.NORMAL,
        linked_ids: list = None,
    ) -> dict:
        """
        Append the new thread message in the log.
        """
        raise NotImplementedError

    @abstractmethod
    async def post_log(
        self, channel_id: Union[int, str], data: PostLogPayload
    ) -> ThreadLogPayload:
        """
        Update log when closing the thread.
        """
        raise NotImplementedError

    @abstractmethod
    async def search_closed_by(self, user_id: Union[int, str]) -> List[dict]:
        """
        Search logs that were closed by specified user.
        """
        raise NotImplementedError

    @abstractmethod
    async def search_by_text(self, text: str, limit: Optional[int]) -> List[dict]:
        """
        Search logs by text.
        """
        raise NotImplementedError

    @abstractmethod
    async def create_note(
        self, recipient: Member, message: Message, message_id: Union[int, str]
    ) -> None:
        """
        Save persistent note into the database.
        """
        raise NotImplementedError

    @abstractmethod
    async def find_notes(self, recipient: Member) -> List[PersistentNotePayload]:
        """
        Find persistent notes of specified user.
        """
        raise NotImplementedError

    @abstractmethod
    async def update_note_ids(self, ids: Dict[str, str]) -> None:
        """
        Update persistent notes IDs (if any) when the recipient opens a new thread.
        """
        raise NotImplementedError

    @abstractmethod
    async def delete_note(self, message_id: Union[int, str]) -> None:
        """
        Delete persistent note from the database.
        """
        raise NotImplementedError

    @abstractmethod
    async def edit_note(self, message_id: Union[int, str], message: str) -> None:
        """
        Edit persistent note in the database.
        """
        raise NotImplementedError

    @abstractmethod
    def get_plugin_partition(self, cog: Cog) -> Collection:
        """
        Get the plugin partition in the database.
        """
        raise NotImplemented


class MongoDBClient(ApiClient):
    def __init__(self, bot: ModmailBot):
        """
        MongoDB client. This class will be used to interact with the database.

        Parameters
        ----------
        bot : ModmailBot
            The Modmail bot.
        """
        mongo_uri = bot.config["connection_uri"]
        if mongo_uri is None:
            mongo_uri = bot.config["mongo_uri"]
            if mongo_uri is not None:
                logger.warning(
                    "You're using the old config MONGO_URI, "
                    "consider switching to the new CONNECTION_URI config."
                )
            else:
                logger.critical("A Mongo URI is necessary for the bot to function.")
                raise RuntimeError

        try:
            mongo_client: MongoClient = AsyncIOMotorClient(mongo_uri)
            db = mongo_client.get_database(f"modmail-{bot.user.id}")
        except ConfigurationError as e:
            logger.critical(
                "Your MongoDB CONNECTION_URI might be copied wrong, try re-copying from the source again. "
                "Otherwise noted in the following message:"
            )
            logger.critical(e)
            sys.exit(0)

        super().__init__(bot, db)

    @property
    def logs(self) -> Collection:
        return self.db.get_collection("logs")

    async def setup_indexes(self) -> None:
        """Setup text indexes so we can use the $search operator"""
        logs = self.logs
        index_name = "messages.content_text_messages.author.name_text_key_text"

        index_info = await logs.index_information()

        # Backwards compatibility
        old_index = "messages.content_text_messages.author.name_text"
        if old_index in index_info:
            logger.info("Dropping old index: %s", old_index)
            await logs.drop_index(old_index)

        if index_name not in index_info:
            logger.info('Creating "text" index for logs collection.')
            logger.info("Name: %s", index_name)
            await logs.create_index(
                [
                    ("messages.content", "text"),
                    ("messages.author.name", "text"),
                    ("key", "text"),
                ]
            )
        logger.debug("Successfully configured and verified database indexes.")

    async def validate_database_connection(self) -> None:
        try:
            await self.db.command("buildinfo")
        except Exception as exc:
            logger.critical("Something went wrong while connecting to the database.")
            logger.critical(f"{type(exc).__name__}: {str(exc)}")

            if isinstance(exc, ServerSelectionTimeoutError):
                logger.critical(
                    "This may have been caused by not whitelisting "
                    "IPs correctly. Make sure to whitelist all "
                    "IPs (0.0.0.0/0) https://i.imgur.com/mILuQ5U.png"
                )

            if isinstance(exc, OperationFailure):
                logger.critical(
                    "This is due to having invalid credentials in your MongoDB CONNECTION_URI. "
                    "Remember you need to substitute `<password>` with your actual password."
                )
                logger.critical(
                    "Be sure to URL encode your username and password (not the entire URL!!), "
                    "https://www.urlencoder.io/, if this issue persists, try changing your username and password "
                    "to only include alphanumeric characters, no symbols."
                    ""
                )
            raise
        else:
            logger.debug("Successfully connected to the database.")
        logger.line("debug")

    async def get_user_logs(self, user_id: Union[str, int]) -> List[ThreadLogPayload]:
        query = {"recipient.id": str(user_id), "guild_id": str(self.bot.guild_id)}
        projection = {"messages": {"$slice": 5}}
        logger.debug("Retrieving user %s logs.", user_id)

        return await self.logs.find(query, projection).to_list(None)

    async def get_latest_user_logs(self, user_id: Union[str, int]) -> ThreadLogPayload:
        query = {
            "recipient.id": str(user_id),
            "guild_id": str(self.bot.guild_id),
            "open": False,
        }
        projection = {"messages": {"$slice": 5}}
        logger.debug("Retrieving user %s latest logs.", user_id)

        return await self.logs.find_one(
            query, projection, limit=1, sort=[("closed_at", -1)]
        )

    async def get_responded_logs(self, user_id: Union[str, int]) -> List[dict]:
        query = {
            "open": False,
            "messages": {
                "$elemMatch": {
                    "author.id": str(user_id),
                    "author.mod": True,
                    "type": {
                        "$in": [
                            ThreadMessageType.ANONYMOUS.value,
                            ThreadMessageType.NORMAL.value,
                        ]
                    },
                }
            },
        }
        return await self.logs.find(query).to_list(None)

    async def get_open_logs(self) -> List[dict]:
        query = {"open": True}
        return await self.logs.find(query).to_list(None)

    async def get_log(
        self, channel_id: Union[str, int], field: str = None
    ) -> ThreadLogPayload:
        if field:
            doc = await self.logs.find_one({"channel_id": str(channel_id)}, {field})
            if doc:
                doc = doc.get(field)
        else:
            logger.debug("Retrieving channel %s logs.", channel_id)
            doc = await self.logs.find_one({"channel_id": str(channel_id)})
        return doc

    async def _get_message_log_raw(
        self, channel: TextChannel, message: Message = None
    ) -> Optional[ThreadMessageLogPayload]:
        if message:
            message_id = str(message.id)
            doc = await self.logs.find_one(
                {
                    "channel_id": str(channel.id),
                    "messages": {
                        "$elemMatch": {
                            "$or": [
                                {"message_id": message_id},
                                {"linked_ids": message_id},
                            ]
                        }
                    },
                },
                {"messages.$"},
            )
        else:
            doc = await self.logs.find_one(
                {"channel_id": str(channel.id)}, {"messages"}
            )
        return doc

    async def get_message_payload(
        self, message: Message, channel: TextChannel
    ) -> Optional[ThreadMessage]:
        doc = await self._get_message_log_raw(channel, message)
        if doc:
            return ThreadMessage(
                key=doc["_id"], raw_data=doc["messages"][0], message=message
            )
        return None

    async def get_message_log_payload(
        self, channel: TextChannel
    ) -> Optional[ThreadMessageLog]:
        doc = await self._get_message_log_raw(channel)
        if doc:
            return ThreadMessageLog(key=doc["_id"], raw_data=doc["messages"])
        return None

    async def get_log_link(self, channel_id: Union[str, int]) -> str:
        doc = await self.get_log(channel_id)
        logger.debug("Retrieving log link for channel %s.", channel_id)
        prefix = self.bot.config["log_url_prefix"].strip("/")
        if prefix == "NONE":
            prefix = ""
        return f"{self.bot.config['log_url'].strip('/')}{'/' + prefix if prefix else ''}/{doc['key']}"

    async def create_log_entry(
        self, recipient: Member, channel: TextChannel, creator: Member
    ) -> str:
        key = secrets.token_hex(6)

        await self.logs.insert_one(
            {
                "_id": key,
                "key": key,
                "open": True,
                "created_at": str(discord.utils.utcnow()),
                "closed_at": None,
                "channel_id": str(channel.id),
                "guild_id": str(self.bot.guild_id),
                "bot_id": str(self.bot.user.id),
                "recipient": {
                    "id": str(recipient.id),
                    "name": recipient.name,
                    "discriminator": recipient.discriminator,
                    "avatar_url": str(recipient.display_avatar.url),
                    "mod": False,
                },
                "creator": {
                    "id": str(creator.id),
                    "name": creator.name,
                    "discriminator": creator.discriminator,
                    "avatar_url": str(creator.display_avatar.url),
                    "mod": isinstance(creator, Member),  # TODO: Check this
                },
                "closer": None,
                "messages": [],
            }
        )
        logger.debug("Created a log entry, key %s.", key)
        prefix = self.bot.config["log_url_prefix"].strip("/")
        if prefix == "NONE":
            prefix = ""
        return f"{self.bot.config['log_url'].strip('/')}{'/' + prefix if prefix else ''}/{key}"

    async def delete_log_entry(self, key: str) -> bool:
        result = await self.logs.delete_one({"key": key})
        return result.deleted_count == 1

    async def delete_all_logs(self) -> int:
        result = await self.logs.delete_many({})
        return result.deleted_count

    async def get_config(self) -> dict:
        conf = await self.db["config"].find_one({"bot_id": self.bot.user.id})
        if conf is None:
            logger.debug("Creating a new config entry for bot %s.", self.bot.user.id)
            await self.db["config"].insert_one({"bot_id": self.bot.user.id})
            return {"bot_id": self.bot.user.id}
        return conf

    async def update_config(self, data: Dict[str, Any]) -> None:
        toset = self.bot.config.filter_valid(data)
        unset = self.bot.config.filter_valid(
            {k: 1 for k in self.bot.config.all_keys if k not in data}
        )

        if toset and unset:
            return await self.db["config"].update_one(
                {"bot_id": self.bot.user.id}, {"$set": toset, "$unset": unset}
            )
        if toset:
            return await self.db["config"].update_one(
                {"bot_id": self.bot.user.id}, {"$set": toset}
            )
        if unset:
            return await self.db["config"].update_one(
                {"bot_id": self.bot.user.id}, {"$unset": unset}
            )

    async def edit_message(self, message_id: Union[int, str], new_content: str) -> None:
        await self.logs.update_one(
            {"messages.message_id": str(message_id)},
            {"$set": {"messages.$.content": new_content, "messages.$.edited": True}},
        )

    async def append_log(
        self,
        message: Union[Message, PartialPersistentNote],
        *,
        message_id: str = "",
        channel_id: str = "",
        type_: ThreadMessageType = ThreadMessageType.NORMAL,
        linked_ids: list = None,
    ) -> dict:
        channel_id = str(channel_id) or str(message.channel.id)
        message_id = str(message_id) or str(message.id)

        # index 0 thread channel msg id, index 1 dm msg id
        linked_ids = [str(msg_id) for msg_id in linked_ids] if linked_ids else []

        data: AppendLogPayload = {
            "timestamp": str(message.created_at),
            "message_id": message_id,
            "linked_ids": linked_ids,
            "author": {
                "id": str(message.author.id),
                "name": message.author.name,
                "discriminator": message.author.discriminator,
                "avatar_url": str(message.author.display_avatar.url),
                "mod": not isinstance(message.channel, DMChannel),
            },
            "content": message.content,
            "type": type_.value,
            "attachments": [
                {
                    "id": a.id,
                    "filename": a.filename,
                    "is_image": a.width is not None,
                    "size": a.size,
                    "url": a.url,
                }
                for a in message.attachments
            ],
        }

        return await self.logs.find_one_and_update(
            {"channel_id": channel_id},
            {"$push": {"messages": data}},
            return_document=True,
        )

    async def post_log(
        self, channel_id: Union[int, str], data: PostLogPayload
    ) -> ThreadLogPayload:
        return await self.logs.find_one_and_update(
            {"channel_id": str(channel_id)}, {"$set": data}, return_document=True
        )

    async def search_closed_by(self, user_id: Union[int, str]) -> List[dict]:
        return await self.logs.find(
            {
                "guild_id": str(self.bot.guild_id),
                "open": False,
                "closer.id": str(user_id),
            },
            {"messages": {"$slice": 5}},
        ).to_list(None)

    async def search_by_text(self, text: str, limit: Optional[int]) -> List[dict]:
        return await self.logs.find(
            {
                "guild_id": str(self.bot.guild_id),
                "open": False,
                "$text": {"$search": f'"{text}"'},
            },
            {"messages": {"$slice": 5}},
        ).to_list(limit)

    async def create_note(
        self, recipient: Member, message: Message, message_id: Union[int, str]
    ) -> None:
        await self.db.notes.insert_one(
            {
                "recipient": str(recipient.id),
                "author": {
                    "id": str(message.author.id),
                    "name": message.author.name,
                    "discriminator": message.author.discriminator,
                    "avatar_url": str(message.author.display_avatar.url),
                },
                "message": message.content,
                "message_id": str(message_id),
            }
        )

    async def find_notes(self, recipient: Member) -> List[PersistentNotePayload]:
        return await self.db.notes.find({"recipient": str(recipient.id)}).to_list(None)

    async def update_note_ids(self, ids: Dict[str, str]) -> None:
        for object_id, message_id in ids.items():
            await self.db.notes.update_one(
                {"_id": object_id}, {"$set": {"message_id": message_id}}
            )

    async def delete_note(self, message_id: Union[int, str]) -> None:
        await self.db.notes.delete_one({"message_id": str(message_id)})

    async def edit_note(self, message_id: Union[int, str], message: str) -> None:
        await self.db.notes.update_one(
            {"message_id": str(message_id)}, {"$set": {"message": message}}
        )

    def get_plugin_partition(self, cog: Cog) -> Collection:
        cls_name = cog.__class__.__name__
        return self.db[f"plugin.{cls_name.lower()}"]


class GitHub:
    """
    The client for interacting with GitHub API.

    Parameters
    ----------
    bot : ModmailBot
        The Modmail bot.
    access_token : str, optional
        GitHub's access token.
    username : str, optional
        GitHub username.
    avatar_url : str, optional
        URL to the avatar in GitHub.
    url : str, optional
        URL to the GitHub profile.

    Attributes
    ----------
    bot : ModmailBot
        The Modmail bot.
    access_token : str
        GitHub's access token.
    username : str
        GitHub username.
    avatar_url : str
        URL to the avatar in GitHub.
    url : str
        URL to the GitHub profile.
    """

    API_BASE_URL = "https://api.github.com/user"

    def __init__(
        self, bot: ModmailBot, access_token: str = None, username: str = None, **kwargs
    ):
        self.bot: ModmailBot = bot
        self.session: ClientSession = bot.session
        self.headers: Optional[Dict[str, str]] = None
        self.access_token: Optional[str] = access_token
        self.username: Optional[str] = username
        self.avatar_url: str = kwargs.pop("avatar_url", "")
        self.url: str = kwargs.pop("url", "")
        if self.access_token:
            self.headers = {"Authorization": f"token {access_token}"}

    @property
    def branch(self):
        return "master" if not self.bot.version.is_prerelease else "development"

    async def login(self):
        """
        Logs in to GitHub and refresh the configuration variable information.
        """

        resp: dict = await self.bot.api.request(
            url=self.API_BASE_URL, headers=self.headers
        )
        if resp.get("login"):
            self.username = resp["login"]
            self.avatar_url = resp["avatar_url"]
            self.url = resp["html_url"]
            logger.info(f"GitHub logged in to: {self.username}")
        else:
            raise ValueError("Invalid github token")

    async def get_user_info(self) -> Optional[dict]:
        """
        Get the GitHub user info that the access token is linked to.
        """
        try:
            await self.login()
        except ValueError:
            return None

        return {
            "user": {
                "username": self.username,
                "avatar_url": self.avatar_url,
                "url": self.url,
            }
        }
