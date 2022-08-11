"""
StarryPy Discord Plugin

Provides a Discord bot that echos conversations between the game server and
a Discord guild channel.

Original authors: kharidiron
"""

import asyncio
import logging
import re

import discord

from base_plugin import BasePlugin
from util.utilities import ChatReceiveMode, ChatSendMode, link_plugin_if_available

from .command_dispatcher import CommandDispatcher

# Mock Objects


class MockPlayer:
    """
    A mock player object for command passing.

    We have to make it 'Mock' because there are all sorts of things in the
    real Player object that don't map correctly, and would cause all sorts
    of headaches.
    """

    name = "DiscordBot"
    logged_in = True

    def __init__(self):
        self.granted_perms = set()
        self.revoked_perms = set()
        self.permissions = set()
        self.priority = 0
        self.name = "MockPlayer"
        self.alias = "MockPlayer"

    def perm_check(self, perm):
        if not perm:
            return True
        elif "special.allperms" in self.permissions:
            return True
        elif perm.lower() in self.revoked_perms:
            return False
        elif perm.lower() in self.permissions:
            return True
        else:
            return False


class MockConnection:
    """
    A mock connection object for command passing.
    """

    def __init__(self, owner):
        self.owner: DiscordPlugin = owner
        self.player = MockPlayer()

    async def send_message(self, *messages):
        for message in messages:
            message = self.owner.color_strip.sub("", message)
            self.owner.bot_write(message, target=self.owner.command_target)
        return None


class DiscordPlugin(BasePlugin, discord.Client):
    name = "discord_bot"
    depends = ["command_dispatcher"]
    default_config = {
        "enabled": True,
        "token": "-- token --",
        "client_id": "-- client_id --",
        "channel": "-- channel id --",
        "staff_channel": "-- channel id --",
        "strip_colors": True,
        "log_discord": False,
        "command_prefix": "!",
        "rank_roles": {"A Discord Rank": "A StarryPy Rank"},
    }

    def __init__(self):
        BasePlugin.__init__(self)
        discord.Client.__init__(self)
        self.enabled = True
        self.mock_connection: MockConnection = None
        self.dispatcher: CommandDispatcher = None
        self.color_strip = re.compile(r"\^(.*?);")
        self.command_target = None
        self.irc_bot_exists = False
        self.irc = None
        self.rank_roles = None
        self.channel: discord.TextChannel = None
        self.staff_channel: discord.TextChannel = None
        self.discord_logger: logging.Logger = None
        self.allowed_commands = (
            "who",
            "help",
            "uptime",
            "motd",
            "show_spawn",
            "ban",
            "unban",
            "kick",
            "list_bans",
            "mute",
            "unmute",
            "set_motd",
            "whois",
            "broadcast",
            "user",
            "del_player",
            "list_players",
            "list_claims",
            "maintenance_mode",
            "shutdown",
            "save",
        )

    def activate(self):
        self.enabled = self.plugin_config["enabled"]
        if not self.enabled:
            return

        BasePlugin.activate(self)
        link_plugin_if_available(self, "chat_manager")
        self.dispatcher = self.plugins.command_dispatcher
        self.irc_bot_exists = (
            link_plugin_if_available(self, "irc_bot")
            and self.plugins["irc_bot"].plugin_config["enabled"]
        )
        if self.irc_bot_exists:
            self.irc = self.plugins["irc_bot"]
        self.mock_connection = MockConnection(self)
        self.rank_roles = self.plugin_config["rank_roles"]

        asyncio.ensure_future(self.start_bot()).add_done_callback(self.error_handler)

        self.discord_logger = logging.getLogger("discord")
        self.discord_logger.setLevel(logging.INFO)
        ch = logging.StreamHandler()
        ch.setFormatter(
            logging.Formatter(
                "%(asctime)s - %(levelname)s - " + "%(name)s # %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        self.discord_logger.addHandler(ch)

    # Packet hooks - look for these packets and act on them

    def on_connect_success(self, data, connection):
        """
        Hook on bot successfully connecting to server.

        :param data:
        :param connection:
        :return: Boolean: True. Must be true, so packet moves on.
        """
        if not self.enabled:
            return True
        asyncio.ensure_future(
            self.make_announce(connection, "joined")
        ).add_done_callback(self.error_handler)
        return True

    def on_client_disconnect_request(self, data, connection):
        """
        Hook on bot disconnecting from the server.

        :param data:
        :param connection:
        :return: Boolean: True. Must be true, so packet moves on.
        """
        if not self.enabled:
            return True
        asyncio.ensure_future(self.make_announce(connection, "left")).add_done_callback(
            self.error_handler
        )
        return True

    def on_chat_sent(self, data, connection):
        """
        Hook on message being broadcast on server. Display it in Discord.

        If 'sc' is True, colors are stripped from game text. e.g. -

        ^red;Red^reset; Text -> Red Text.

        :param data:
        :param connection:
        :return: Boolean: True. Must be true, so packet moves on.
        """
        if not self.enabled:
            return True

        dispatcher_prefix = self.dispatcher.plugin_config["command_prefix"]
        msg = data["parsed"]["message"]

        if not msg.startswith(dispatcher_prefix):
            if self.plugin_config["strip_colors"]:
                msg = self.color_strip.sub("", msg)

            if data["parsed"]["send_mode"] == ChatSendMode.UNIVERSE:
                if self.plugins["chat_manager"]:
                    if not self.plugins["chat_manager"].mute_check(connection.player):
                        alias = connection.player.alias
                        self.bot_write(f"**<{alias}>** {msg}")
        return True

    # Helper functions - Used by commands

    async def start_bot(self):
        """
        :param :
        :param :
        :return: Null
        """
        self.logger.info("Starting Discord Bot")
        try:
            await self.login(self.plugin_config["token"])
            await self.connect()
        except Exception as e:
            self.logger.exception(e)

    def on_ready(self):
        self.channel = self.get_channel(int(self.plugin_config["channel"]))
        self.staff_channel = self.get_channel(int(self.plugin_config["staff_channel"]))
        if not self.channel:
            self.logger.error(
                "Couldn't get channel! Messages can't be "
                "sent! Ensure the channel ID is correct."
            )
        if not self.staff_channel:
            self.logger.warning(
                "Couldn't get staff channel! Reports "
                "will be sent to the main channel."
            )

    async def on_message(self, message):
        await self.send_to_game(message)

    async def send_to_game(self, message: discord.Message):
        """
        Broadcast a message on the server. Make sure it isn't coming from the
        bot (or else we get duplicate messages).

        :param message: The message packet.
        :return: Null
        """
        if message.author.bot:
            return
        if not message.channel.id in (
            int(self.plugin_config["channel"]),
            int(self.plugin_config["staff_channel"]),
        ):
            return

        nick = message.author.display_name
        text = message.clean_content
        server = message.guild

        if message.content[0] == self.plugin_config["command_prefix"]:
            self.command_target = message.channel
            asyncio.ensure_future(
                self.handle_command(message.content[1:], message.author)
            )

        elif message.channel == self.channel:
            for emote in server.emojis:
                text = text.replace(
                    f"<:{emote.name}:{emote.id}>",
                    f":{emote.name}:",
                )
            await self.factory.broadcast(
                f"[^orange;DC^reset;] <{nick}> {text}",
                mode=ChatReceiveMode.BROADCAST,
            )
            if self.config.get_plugin_config(self.name)["log_discord"]:
                self.logger.info(f"<{nick}> {text}")
            if self.irc_bot_exists:
                asyncio.ensure_future(self.irc.bot_write(f"[DC] <{nick}> {text}"))

    async def make_announce(self, connection, circumstance):
        """
        Send a message to Discord when someone joins/leaves the server.

        :param connection: Connection of connecting player on server.
        :param circumstance:
        :return: Null.
        """
        await asyncio.sleep(1)
        self.bot_write(f"**{connection.player.alias}** has {circumstance} the server.")

    async def handle_command(self, data, user):
        split = data.split()
        command = split[0]
        to_parse = split[1:]
        roles = sorted(user.roles, reverse=True)
        role = "Guest"
        for x in roles:
            if x.name in self.rank_roles:
                role = self.rank_roles[x.name]
                break
        self.mock_connection.player.permissions = self.plugins.player_manager.ranks[
            role.lower()
        ]["permissions"]
        self.mock_connection.player.priority = self.plugins.player_manager.ranks[
            role.lower()
        ]["priority"]
        self.mock_connection.player.alias = user.display_name
        self.mock_connection.player.name = user.display_name
        if command in self.dispatcher.commands:
            # Only handle commands that work from Discord
            if command in self.allowed_commands:
                await self.dispatcher.run_command(
                    command, self.mock_connection, to_parse
                )
            else:
                self.bot_write(
                    "Command not handled by Discord.", target=self.command_target
                )
        else:
            self.bot_write("Command not found.", target=self.command_target)

    def bot_write(self, msg, target=None):
        if target is None:
            target = self.channel
        if target is None:
            return
        asyncio.ensure_future(target.send(msg)).add_done_callback(self.error_handler)

    def error_handler(self, future):
        try:
            future.result()
        except Exception as e:
            self.logger.error(
                "Caught an unhandled exception in Discord bot.  Will restart."
            )
            self.logger.exception(e)
            asyncio.ensure_future(self.start_bot())
