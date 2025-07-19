import asyncio
import uuid
from typing import Callable, Optional
from telethon import TelegramClient, events, errors
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
            'bot_' + str(uuid.uuid4()),
            self.api_id,
            self.api_hash
        ).start(bot_token=self.bot_token)
        
        # self.known_commands = []
        
        self.add_handler(self.start_command_handler, '/start')
        self.add_handler(self.group_command_handler, '/group')
        self.add_handler(self.test_password, '/password')
        self.add_handler(self.test_user_verification, "/user")
        self.add_handler(self.test_admin_verification, "/admin")
        self.add_handler(self.digits, events.NewMessage(incoming=True, pattern=re.compile(r'[0-9]+')))
        
        self.add_handler(self.unknown_command_handler)

        self.group = {
            "Lukas":0, "Heiko":0, "Barnie":0, "Klaus":0, "Hans":0,
            "David":0, "Jens":0, "Jürgen":0, "Ralf":0, "Rainer":0,
            "Jörg":0, "Johannes":0, "Max":0, "Peter":0, "Karlo":0,
            "Annie":0, "Marie":0, "Lena":0, "Lara":0, "Ruberta":0,
            "Susi1":0, "Susi2":0, "Susi3":0, "Susi4":0, "Susi5":0,
            "Marx1":0, "Marx2":0, "Marx3":0, "Marx4":0, "Marx5":0,
            "Leon1":0, "Leon2":0, "Leon3":0, "Leon4":0, "Leon5":0
        }
        self.current_page = 0
        self.current_group = None

        self.latest_messages = []
        
    async def run(self):
        print("!!!! start message vanisher")
        asyncio.create_task(self.message_vanisher()) 
        
        await self.bot.run_until_disconnected()       
        
    ### SECTION: handler administration
    
    def add_handler(self, handler, 
                    event: Optional[str | events.common.EventBuilder] = None, 
                    exception_handler: Optional[Callable] = None):
        
        if isinstance(event, str):
            event = events.NewMessage(pattern=event)
            # self.known_commands.append(event)
        elif event == None:
            event = events.NewMessage()
            
        if exception_handler is None:
            exception_handler = self.exception_handler
            
        async def wrapped_handler(event):
            message = event.message
            self.add_latest_message(message, True, True)
            await handler(event)
            
            # print("stop propagation")
            # NOTE: ist es ok, wenn ich die propagation hier stoppe?
            raise events.StopPropagation
            
        wrapped_handler_with_exception = exception_handler(wrapped_handler)
        self.bot.add_event_handler(wrapped_handler_with_exception, event)
        
    
    def exception_handler(self, func):
        async def wrapper(event, *args, **kwargs): 
            try: 
                sender_id = event.sender_id
                try:
                    return await func(event, *args, **kwargs)
                except exceptions.VerificationException as e:
                    print(f"Verification Exception caught: {e}")
                    await self.send_message(sender_id, e.message, True, True)
                    raise events.StopPropagation
                    # return False
                    
                except asyncio.TimeoutError as e:
                    print(f"Timeout Exception caught: {e}")
                    await self.send_message(sender_id, "Your request has expired. Please start again from the beginning.")
                    return False
                    
            except AttributeError as e:
                print("Not a valid event given.", e)
            except asyncio.TimeoutError as e:
                print(f"TimeoutError: {e}")
            except errors.rpcerrorlist.FloodWaitError as e:
                print(f"FloodWaitError: {e}")
            except errors.rpcerrorlist.UserIsBlockedError as e:
                print(f"UserIsBlockedError: {e}")
                
        return wrapper
    
    
    # subfunction of message_vanisher to make code more readable
    async def delete_oldest_message(self):
        message = self.latest_messages.pop(0)
        if isinstance(message, list):
            await asyncio.gather(*(m.delete() for m in message))
        else:
            await message.delete()
    
    def get_latest_messages_length(self):
        length = []
        for m in self.latest_messages:
            if isinstance(m, list):
                length.append(len(m))
            else:
                length.append(True)
        return length
    
    # NOTE: In theory, this is easier but does not work with consecutive lists
    # async def message_vanisher(self):
    #     while True:
    #         await asyncio.sleep(10)
            
    #         while len(self.latest_messages) > 3:
    #             self.delete_oldest_message()
            
    #         if len(self.latest_messages) == 0:
    #             continue
            
    #         i = 0
    #         while i < len(self.latest_messages):
    #             if isinstance(self.latest_messages[i], list):
    #                 while i > 0:
    #                     if (i < len(self.latest_messages) - 1):
    #                         self.delete_oldest_message()
    #                     i -= 1
    #                 break
    #             i += 1

    # IDEA: Maybe add an overall timer, that gets reset after each new message and just deletes all messages after 1h or so
    async def message_vanisher(self):
        while(True):
            # print("start loop - length:", len(self.latest_messages), " content:", self.get_latest_messages_length())
            await asyncio.sleep(10)
            
            if len(self.latest_messages) == 0:
                continue
            
            i = 0
            while( i < len(self.latest_messages)):
                # delete older messages if list is longer than 3
                if len(self.latest_messages) > 3:
                    await self.delete_oldest_message()
                    continue
                
                # Check for lists in the remaining messages and delete everything before a list 
                if isinstance(self.latest_messages[i], list):
                    # print("i:", i, "length - 2:", len(self.latest_messages) - 1)
                    for j in range(i):
                        if ( j >= len(self.latest_messages) - 2):
                            break
                
                        await self.delete_oldest_message()
        
                i += 1                
                
            
    ### SECTION: Communication
    
    # TODO: at timeouts for certain functions using a decorator
    
    def add_latest_message(self, message, conv = False, new = False):
        if new == True:
            self.latest_messages.append([message])
        elif conv == True:
            if len(self.latest_messages) > 0 and isinstance(self.latest_messages[-1], list):
                self.latest_messages[-1].append(message)
            else:
                self.latest_messages.append([message]) 
        else:
            self.latest_messages.append(message)
            
    # NOTE: CHange conv to default True and remove True, True from all function
    async def send_message(self, user_id, text, vanish = True, conv = False):
        try:
            message = await self.bot.send_message(user_id, text)
            if vanish == True:
                self.add_latest_message(message, conv)

            return message
        except Exception as e:
            print(f"Failed to send message to user {user_id}: {e}")
            
            
    # Send a keyboard to the user (can be inline or reply) 
    # NOTE: Mybe include this into the send_message method 
    async def send_keyboard(self, user_id, text, keyboard_layout, vanish = True, conv = None):
        try:
            message = await self.bot.send_message(
                user_id,
                text,
                buttons=keyboard_layout,
                parse_mode='html'
            )
            if vanish == True:
                self.add_latest_message(message, conv)

            return message
        except Exception as e:
            print(f"Failed to send keyboard to user {user_id}: {e}")
            
            
    def keyboard_callback(self, user_id):
        return events.CallbackQuery(func = lambda e: e.sender_id == user_id)



    ### SECTION: Event Handlers
        
    async def unknown_command_handler(self, event):
        sender_id = event.sender_id
        message = event.message.message
        
        await self.send_message(sender_id, f"**{message}** is an unknown command.", True, True)


    async def test_password(self, event):
        # get the message and cut off the /password part
        message = event.message.message
        print("message: ", message)
        try:
            password = message.split(" ")[1]
            
            password_correct = await handlers.check_password(password, dep.get_repo())
            print("Pass word is correct:", password_correct)
            
        # TODO: improve exceptions
        except Exception as e:
            print("password was not provided", e) 
            
            
    async def digits(self, event):
        user_id = event.sender_id
        await self.send_message(user_id, f'catches digits: {event.text}', True, True)
        
        # raise events.StopPropagation
            
            
    @dep.verify_user
    async def test_user_verification(self, event):
        user_id = event.sender_id
        await self.send_message(user_id, "You are a registered user.", True, True)

        
    @dep.verify_admin    
    async def test_admin_verification(self, event):
        user_id = event.sender_id
        await self.send_message(user_id, "You are a registered admin.", True, True)
    
    
    async def start_command_handler(self, event):
        # sender = await event.get_sender()
        user_id = event.sender_id
        print("sender id:", user_id)
        # print(sender.stringify())
    
        # Check if user is already registered
        if await handlers.check_user(user_id, dep.get_repo()) == True:
            await self.send_message(user_id, "There is nothing more to do. You are already registered.", True, True)
            return 
        
        # do registration
        await self.register_conversation(user_id)
            

    # NOTE: add user verification
    async def group_command_handler(self, event):
        user_id = event.sender_id
        await self.group_selection(user_id)
        
    
    ###### Communication formats:
    
        
    ### SECTION: Conversations
    
    async def register_conversation(self, user_id):        
        async with self.bot.conversation(user_id) as conv:
            chat = await conv.get_input_chat()

            # Start registration process (TODO: put into its own function)
            message_register = await self.send_keyboard(chat, "Do you want to register?", self.keyboard_confirm, True, True)
            button_event: events.CallbackQuery.Event = await conv.wait_event(self.keyboard_callback(user_id), timeout = 30)
            data = button_event.data.decode('utf8')
            if data == "No":
                await message_register.edit(f"Register process aborted.", buttons=None)
                return False
            await message_register.edit(f"Start Register process.", buttons=None)

            # Password request
            if self.request_authentication(conv) == False:
                return False

            # Retrieve user info
            

    async def request_authentication(self, conv):
        chat = await conv.get_input_chat()
        
        message_password = await self.send_message(chat, "Please enter the password:", True, True)
        max_tries = 3
        tries = 0
        authenticated = False
        while (tries < max_tries):
            password_event = await conv.wait_event(events.NewMessage(incoming=True), timeout = 30)
            password = password_event.message.message
            await password_event.message.delete()
            
            authenticated = await handlers.check_password(password, dep.get_repo())
            if authenticated == True:
                return True
            else:
                await self.send_message(chat, "Password incorrect. Please try again.", True, True)
                tries += 1
                
        if authenticated == False:
            await self.send_message(chat, "Too many tries. Aborting registration.", True, True)
            return False
    

    # TODO: Add user verification
    # TODO: implement this in a more modular way
    async def group_selection(self, user_id):    
        async with self.bot.conversation(user_id) as conv:
            chat = await conv.get_input_chat()
                        
            message = await self.send_keyboard(chat, "Group View", self.getGroupKeyboard(), True, True)
            submitted = False
            
            while True:          
                button_event = await conv.wait_event(self.keyboard_callback(user_id), timeout = 180)
                button_data = button_event.data.decode('utf8')

                await button_event.answer()
                                            
                if button_data == "group_submit":
                    await message.edit("Submitted", buttons=None)
                    submitted = True
                    break
                elif button_data == "group_cancel":
                    # TODO: reset the group to initial state and set current_page to 0
                    await message.edit("Canceled", buttons=None)
                    break
                
                elif "group_plus" in button_data:
                    name = button_data.split("_")[2]
                    self.group[name] += 1
                elif "group_minus" in button_data:
                    name = button_data.split("_")[2]
                    self.group[name] -= 1 if self.group[name] > 0 else 0
                    
                elif "group_next" in button_data:
                    # TODO: replace this by the actual nummber of maximal pages
                    self.current_page = min(self.current_page + 1, 2)
                elif "group_prev" in button_data:
                    self.current_page = max(self.current_page - 1, 0)
                    
                group = self.getGroupKeyboard()
                if self.current_group == group:
                    continue
                
                self.current_group = group
                await message.edit(buttons=group)
                
            if submitted == True:
                total = sum(self.group.values())
                message = f"added **{total}** coffees:\n"
                for name, value in self.group.items():
                    if value != 0:
                        message += f"\t{name}: {value}\n"
                print(message)
                await self.send_message(chat, message, False)
                
                # TODO: do something with content of self.group
                
            
            # TODO: reset group to initial state
            
    
    ### helper functions
            
        
            
            
    ### keyboards
    
    keyboard_confirm = [
        [  
            Button.inline("Yes", b"Yes"), 
            Button.inline("No", b"No")
        ],
    ]
    
    
    def getGroupKeyboard(self) -> list:
        keyboard_group = []
        total = sum(self.group.values())
        
        items = list(self.group.items())
        pages = len(items) // 15
        # last_page = len(items) % 15
        
        i_start =   self.current_page * 15
        i_end   = ((self.current_page + 1) * 15) if (self.current_page < pages) else None #self.current_page * 15 + last_page
        
        for name, value in items[i_start : i_end]:
            keyboard_group.append([
                Button.inline(str(name), "group_name"),
                Button.inline(str(value), "group_value"),
                Button.inline("+", f"group_plus_{name}"),
                Button.inline("-", f"group_minus_{name}")
            ])
            
        if pages > 0:
            navigation_buttons = []
            if self.current_page > 0:
                navigation_buttons.append(
                    Button.inline("prev", "group_prev")
                )
                
            if self.current_page < pages:
                navigation_buttons.append(
                    Button.inline("next", "group_next")
                )
    
            if navigation_buttons != []:
                keyboard_group.append(navigation_buttons)
            
        keyboard_group.append([
            Button.inline("Cancel", "group_cancel")
        ])
        
        if total > 0:
            keyboard_group[-1].append(Button.inline(f"Submit ({total})", "group_submit"))
        
        return keyboard_group