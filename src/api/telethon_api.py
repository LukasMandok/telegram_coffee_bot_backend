from telethon import TelegramClient, events
from telethon.tl.types import UpdateShortMessage

# from ..bot import handlers

class TelethonAPI:
    def __init__(self, api_id, api_hash, bot_token):
        self.api_id = api_id
        self.api_hash = api_hash
        self.bot_token = bot_token
        
        print("initialize and start bot: api_id: {}, api_hash: {}, bot_token: {}".format(api_id, api_hash, bot_token))
        
        self.bot = TelegramClient(
            'bot',
            self.api_id,
            self.api_hash
        ).start(bot_token=self.bot_token)
        
        self.bot.add_event_handler(self.start_command_handler, events.NewMessage(pattern='/start'))
        
    async def run(self):
        await self.bot.run_until_disconnected()        
        
    async def receive_message(self, event):
        print(event.stringify())
        
    async def start_command_handler(self, event):
        sender = await event.get_sender()
        print(sender.stringify())
        