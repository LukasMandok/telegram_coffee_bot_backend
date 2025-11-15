# Telegram Coffee Tally Bot

Python Docker bot for managing a coffee list with MongoDB and optional Google Sheets integration.

## 🚀 Quick Start

One-liner setup (Linux/Mac/Git Bash):
```bash
curl -L https://raw.githubusercontent.com/LukasMandok/telegram_coffee_bot_backend/main/scripts/setup.sh | bash
```

Or Windows (PowerShell):
```powershell
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/LukasMandok/telegram_coffee_bot_backend/main/scripts/setup.ps1" -OutFile "setup.ps1"; .\setup.ps1
```

## 📚 Documentation

- **[scripts/README.md](scripts/README.md)** - Setup scripts and quick start guide
- **[DOCKER.md](DOCKER.md)** - Docker deployment guide and advanced options
- **[DEVELOPMENT.md](DEVELOPMENT.md)** - Development setup and guidelines

---

## Copyright Disclaimer
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

        


### Google Spreadsheet Setup (optional)

1. Goto [Google Cloud API](console.cloud.google.com) and agree to the terms 
2. Select active apis and services and create a new project there
3. Press on activate apis and services
4. Search for Google Sheets API and activate that one
5. create new login data for the Google Sheets API and use application data as qualification
6. give your service account a name and an id (e.g. coffee-bot)
7. Afterwards goto login data on the sidebar and select your newly created service account on the bottom
8. Goto keys and add a new JSON key on the bottom dropdown and save that newly generated key on your PC
9. Also copy the email address of your service account.

9. Now go to your personal google account spread sheets and create a new spreadsheet for the coffee bot.
10. On the top right, you press on the button to add new users and enter your service account mail address, to add this one as a co-worker .

For your application instance to authenticate to your speadsheet, you have to enter the client ID and private secret to your environment variables. 


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

### Running the Docker Container

To run the Docker container for the backend, follow these steps:

1. **Set Environment Variables**:
   Ensure the following environment variables are set in a `.env` file or directly in your system:
   - `BOT_TOKEN`: Your Telegram bot token.
   - `BOT_HOST`: The host for the bot.
   - `API_ID`: Your Telegram API ID.
   - `API_HASH`: Your Telegram API hash.
   - `DATABASE_URL`: The URL for your MongoDB database.

2. **Build the Docker Image**:
   Run the following command to build the Docker image:
   ```bash
   docker-compose build
   ```

3. **Start the Container**:
   Run the following command to start the container:
   ```bash
   docker-compose up
   ```

4. **Access the Application**:
   - The backend will be accessible on port `8000`.
   - Ensure MongoDB is running and accessible if required.

5. **Stop the Container**:
   To stop the container, use:
   ```bash
   docker-compose down
   ```

### Setting up a Virtual Python Environment for  Development

To set up a virtual Python environment for development, follow these steps:

1. **Create a Virtual Environment**:
   Navigate to the backend directory and run:
   ```bash
   python -m venv venv
   ```
   This will create a virtual environment in a folder named `venv`.

2. **Activate the Virtual Environment**:
   - On Windows:
     ```bash
     .\venv\Scripts\activate
     ```
   - On macOS/Linux:
     ```bash
     source venv/bin/activate
     ```

3. **Install Dependencies**:
   Once the virtual environment is activated, install the required dependencies:
   ```bash
   pip install -r src/requirements.txt
   ```

4. **Run the Application**:
   You can now run the Python scripts directly for testing:
   ```bash
   python src/main.py
   ```

5. **Deactivate the Virtual Environment**:
   When you're done, deactivate the virtual environment by running:
   ```bash
   deactivate
   ```

#### Run unit test

1. **Virtual Eenvironmant