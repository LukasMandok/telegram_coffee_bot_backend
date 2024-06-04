### Telegram Coffee Tally Bot
python docker bot for managing a coffee list spreadsheet on google spreadsheet

#### Copyright Disclaimer:
This code for the docker deployment is mostly copied & adapted from **xstar97** using the **reddit-auto-reply** bot as a baseline:
[Link to github repo](https://github.com/xstar97/reddit-auto-reply)

#### Important sources:

##### cron-telebot: 
nice implementation of python-telegram-bot with fastapi
[source code](https://github.com/hsdevelops/cron-telebot/tree/main)


##### Tutorial for python-telegram-bot:
[tutorial](https://github.com/python-telegram-bot/python-telegram-bot/wiki/Introduction-to-the-API)

##### official WebApp example for python-telegram-bot:
[example](https://github.com/python-telegram-bot/python-telegram-bot/blob/master/examples/webappbot.py)



### Concepts:

#### Onboarding:
1. Telegram User is send to backend
2. Backend checks wether user is already in the database -> using handler (login_user)
    - In case this returns false:
        a. User has to enter a password
        b. password is checked via @post(/password) and the corresponding handler with the database.
            - deny access in case of wrong password, otherwise continue
        c. First name is checked for in the spreadsheet
        d. in case user name is missing , user is asked to specify his firstname
        e. user added to database
        f. new entry in spreadsheet is added


#### Logging:

        





### Docker configuration:
```yaml
version: "0.0.1"

services:
  reddit-auto-reply:
    image: ghcr.io/lukasmandok/telegram_coffee_bot_backend:latest
    ports:
      - "8080:3000"
    environment:
      - TELEGRAM_TOKEN=${TELEGRAM_TOKEN}
      - BOT_HOST=${BOT_HOST}

      - GSHEET_SSID=${GSHEET_SSID}
    #   - BOT_STATE=production
    #   - REDIS_HOST=localhost
    #   - REDIS_PASSWORD=password
    #   - REDIS_PORT=6379
    #   - CLIENT_ID=reddit_client_id
    #   - CLIENT_SECRET=reddit_client_secret
    #   - USERNAME=REDDIT_USER
    #   - PASSWORD=REDDIT_PASS
    #   - EXCLUDE_USERS=user1,user2 #delim by ,
    #   - COMMENT_TEXT="Hello world!"
    restart: unless-stopped
```