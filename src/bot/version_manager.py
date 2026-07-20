import re
from ..models.beanie_models import BotMetadata, TelegramUser
from typing import Any

async def get_latest_version_from_file() -> tuple[str, str]:
    with open("CHANGELOG.md", "r") as f:
        content = f.read()
    
    match = re.search(r"## \[(\d+\.\d+\.\d+)\]", content)
    
    if match is None:
        # Fallback if no version is found in the file
        return "0.0.0", content
        
    return match.group(1), content

async def check_and_notify_updates(api: Any):
    latest_version, changelog = await get_latest_version_from_file()
    meta = await BotMetadata.get("bot_metadata")
    
    if not meta:
        await BotMetadata(current_version=latest_version).create()
        return

    if meta.current_version != latest_version:
        # 1. Notify all users
        users = await TelegramUser.find_all().to_list()
        message = f"🚀 **New Version {latest_version} is here!**\n\n{changelog}"
        
        for user in users:
            try:
                await api.message_manager.send_text(user.id, message)
            except Exception as e:
                # Log error (e.g., user blocked bot)
                pass
        
        # 2. Update DB
        meta.current_version = latest_version
        await meta.save()