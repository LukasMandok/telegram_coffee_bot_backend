from typing import Callable, Optional
from telethon import TelegramClient, events
from telethon import Button
from telethon.tl.types import UpdateShortMessage

from ..handlers import handlers, exceptions
from  ..dependencies import dependencies as dep

# from ..bot import handlers


### API Handler

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
        
        self.add_handler(self.start_command_handler, '/start')
        self.add_handler(self.test_password, '/password')
        self.add_handler(self.test_user_verification, "/user")
        self.add_handler(self.test_admin_verification, "/admin")
    
    async def run(self):
        await self.bot.run_until_disconnected()        
        
    ### SECTION: handler administration
    
    def add_handler(self, handler, 
                    event: Optional[str | events.common.EventBuilder] = None, 
                    exception_handler: Optional[Callable] = None):
        
        if isinstance(event, str):
            event = events.NewMessage(pattern=event)
            
        if exception_handler is None:
            exception_handler = self.exception_handler
            
        wrapped_handler = exception_handler(handler)
        self.bot.add_event_handler(wrapped_handler, event)
        
    
    def exception_handler(self, func):
        async def wrapper(event, *args, **kwargs): 
            try:
                return await func(event, *args, **kwargs)
            except exceptions.VerificationException as e:
                print(f"Verification Exception caught: {e}")
                
                try:
                    sender_id = event.sender_id
                    await self.send_message(sender_id, "You are not a registered user.")
                except Exception as e:
                    print("Not a valid event given, cannot send message to user: ", e)
                finally:
                    return False
                
        return wrapper
            
    ### SECTION: Communication
            
    async def send_message(self, user_id, text):
        try:
            await self.bot.send_message(user_id, text)
            print(f"Message sent to user {user_id}: {text}")
        except Exception as e:
            print(f"Failed to send message to user {user_id}: {e}")
            
    ### SECTION: Event Handlers
        
    # TODO: the password don't seem to match at the moment
    # I need to debug this 
    async def test_password(self, event):
        # print(event.stringify())
        
        # get the message and cut off the /password part
        message = event.message.message
        print("message: ", message)
        try:
            password = message.split(" ")[1]
            
            password_correct = await handlers.check_password(password, dep.get_repo())
            print("Pass word is correct:", password_correct)
            
        except Exception as e:
            print("password was not provided", e) 
            
    @dep.verify_user_decorator
    async def test_user_verification(self, event):
        sender_id = event.sender_id
        await self.send_message(sender_id, "You are a registered user.")

        
    @dep.verify_admin_decorator    
    async def test_admin_verification(self, event):
        sender_id = event.sender_id
        await self.send_message(sender_id, "You are a registered admin.")
    
    
    async def start_command_handler(self, event):
        # sender = await event.get_sender()
        sender_id = event.sender_id
        print("sender id:", sender_id)
        # print(sender.stringify())
        
        user_registered = await handlers.check_user(sender_id, dep.get_repo())
        print("user registered:", user_registered)
        
        if user_registered == True:
            return 
        
        else:
            pass
            
    
    ###### Communication formats:
    @classmethod
    async def create_group_keyboard(user_list, ):
        pass
    
        
        
        
    ###### Communication with client:async def send_keyboard(self, user_id, text, keyboard_layout):
    
    # Send a keyboard to the user (can be inline or reply) 
    async def send_keyboard(self, user_id, text, keyboard_layout):
        await self.bot.send_message(
            user_id,
            text,
            buttons=keyboard_layout,
            parse_mode='html'
        )