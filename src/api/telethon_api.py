from typing import Callable, Optional
from telethon import TelegramClient, events
from telethon import Button
from telethon.tl.types import UpdateShortMessage

from ..handlers import handlers, exceptions
from  ..dependencies import dependencies as dep

import re


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
        self.add_handler(self.group_command_handler, '/group')
        self.add_handler(self.test_password, '/password')
        self.add_handler(self.test_user_verification, "/user")
        self.add_handler(self.test_admin_verification, "/admin")
        self.add_handler(self.digits, events.NewMessage(incoming=True, pattern=re.compile(r'[0-9]+')))

        self.group = {"Lukas":0, "Heiko":0}
        
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
            
    # Send a keyboard to the user (can be inline or reply) 
    # NOTE: Mybe include this into the send_message method 
    async def send_keyboard(self, user_id, text, keyboard_layout):
        try:
            return await self.bot.send_message(
                user_id,
                text,
                buttons=keyboard_layout,
                parse_mode='html'
            )
        except Exception as e:
            print(f"Failed to send keyboard to user {user_id}: {e}")
            
            
    def keyboard_callback(self, user_id):
        return events.CallbackQuery(func = lambda e: e.sender_id == user_id)

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
            
    async def digits(self, event):
        await self.bot.send_message(event.sender_id, f'catches digits: {event.text}')
        raise events.StopPropagation
            
            
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
        user_id = event.sender_id
        print("sender id:", user_id)
        # print(sender.stringify())
        
        user_registered = await handlers.check_user(user_id, dep.get_repo())
        print("user registered:", user_registered)
        
        if user_registered == True:
            return 
        
        else:
            await self.register_conversation(user_id)
            
            
    async def group_command_handler(self, event):
        user_id = event.sender_id
        await self.group_selection(user_id)
    
    ###### Communication formats:
    @classmethod
    async def create_group_keyboard(user_list, ):
        pass
    
        
    ### conversations:
    async def register_conversation(self, user_id):
        async with self.bot.conversation(user_id) as conv:
            chat = await conv.get_input_chat()
            message = await self.send_keyboard(chat, "Do you want to register?", self.keyboard_confirm)
            
            button_event: events.CallbackQuery.Event = await conv.wait_event(self.keyboard_callback(user_id))
            print("register_conversation - press:", button_event)
            data = button_event.data.decode('utf8')
            
            await message.edit(f"Received: {data}", buttons=None)
    
    
    # TODO: implement this in a more modular way
    async def group_selection(self, user_id):
        async with self.bot.conversation(user_id) as conv:
            chat = await conv.get_input_chat()
            message = await self.send_keyboard(chat, "Group View", self.getGroupKeyboard())
            submitted = False
            
            while True: 
                current_group = self.group.copy()           
                button_event = await conv.wait_event(self.keyboard_callback(user_id))
                button_data = button_event.data.decode('utf8')

                await button_event.answer()
                                            
                if button_data == "group_submit":
                    await message.edit("Submitted", buttons=None)
                    submitted = True
                    break
                elif button_data == "group_cancel":
                    await message.edit("Canceled", buttons=None)
                    break
                elif "group_plus" in button_data:
                    name = button_data.split("_")[2]
                    self.group[name] += 1
                    
                elif "group_minus" in button_data:
                    name = button_data.split("_")[2]
                    self.group[name] -= 1 if self.group[name] > 0 else 0
                
                if self.group == current_group:
                    continue
                
                await message.edit(buttons=self.getGroupKeyboard())
                
            if submitted == True:
                message = "added the following coffes:\n"
                for name, value in self.group.items():
                    if value != 0:
                        message += f"\t{name}: {value}\n"
                print(message)
                await self.send_message(chat, message)
                
                # TODO: do something with content of self.group
                
            
            # TODO: reset group to initial state
            
    ### keyboards
    
    keyboard_confirm = [
        [  
            Button.inline("Yes", b"Yes"), 
            Button.inline("No", b"No")
        ],
    ]
    
    
    def getGroupKeyboard(self) -> list:
        keyboard_group = []
        for name, value in self.group.items():
            keyboard_group.append([
                Button.inline(str(name), "group_name"),
                Button.inline(str(value), "group_value"),
                Button.inline("+", f"group_plus_{name}"),
                Button.inline("-", f"group_minus_{name}")
            ])
        keyboard_group.append([
            Button.inline("Submit", "group_submit"),
            Button.inline("Cancel", "group_cancel")
        ])
        return keyboard_group