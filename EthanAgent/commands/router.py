from asyncio import Handle
from typing import Callable


class CommandRouter:
    def __init__(self):
        self.commands: dict[str, Callable] = {}

    def register(self, cmd: str, handler: Callable) -> None:
        self.commands[cmd] = handler
    
    def is_slash_command(self, text: str) -> bool:
        cmd = text.strip().lower()
        if cmd in self.commands:
            return True
        return False

    async def dispatch(self, query: str) -> None:
        cmd = query.strip().lower()
        if cmd in self.commands:
            return await self.commands[cmd](query)
        return None

    