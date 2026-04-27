from commands.router import CommandRouter

async def cmd_test(query: str) -> None:
    content = "Test command executed."
    return content

async def cmd_help(query: str) -> None:
    content = "Help command executed."
    return content

def register_commands(router: CommandRouter) -> None:
    router.register("/test", cmd_test)
    router.register("/help", cmd_help)