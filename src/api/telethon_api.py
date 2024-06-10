from telethon import TelegramClient, events
from telethon.tl.types import UpdateShortMessage

from ..handlers import handlers  
from ..dependencies.dependencies import get_repo

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
        self.bot.add_event_handler(self.test, events.NewMessage(pattern='/password'))
        
    async def run(self):
        await self.bot.run_until_disconnected()        
        
        
    # TODO: the password don't seem to match at the moment
    # I need to debug this 
    async def test(self, event):
        # print(event.stringify())
        
        # get the message and cut off the /password part
        message = event.message.message
        password = message.split(" ")[1]
        
        password_correct = await handlers.check_password(password, get_repo())
        
        print("Pass word is correct:", password_correct)
        
    async def start_command_handler(self, event):
        # sender = await event.get_sender()
        sender_id = event.sender_id
        print("sender id:", sender_id)
        # print(sender.stringify())
        
        user_registered = await handlers.check_user(sender_id, get_repo())
        print("user registered:", user_registered)