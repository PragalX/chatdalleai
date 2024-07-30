import logging
import os
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.types import ParseMode
from aiogram.utils import executor
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import requests
import asyncio
import boto3

# Load environment variables from .env file
load_dotenv()

# Initialize logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# Environment variables
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
BOT_OWNER_ID = int(os.getenv("BOT_OWNER_ID"))
LOG_CHANNEL_ID = os.getenv("LOG_CHANNEL_ID")
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_REGION", "us-west-2")

# Initialize OpenAI
def generate_image(prompt):
    try:
        url = "https://api.openai.com/v1/images/generations"
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "dall-e-3",
            "prompt": prompt,
            "n": 1,
            "size": "1024x1024"
        }
        response = requests.post(url, headers=headers, json=payload)
        response_data = response.json()
        if 'data' in response_data:
            return response_data['data'][0]['url']
        else:
            log.error(f"Error in response: {response_data}")
            return None
    except Exception as e:
        log.error(f"Error generating image: {e}")
        return None

# Initialize bot and dispatcher
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher(bot)
dp.middleware.setup(LoggingMiddleware())

# MongoDB client
client = AsyncIOMotorClient(MONGO_URI)
db = client['telegram_bot']
users_collection = db['users']
groups_collection = db['groups']
subscriptions_collection = db['subscriptions']
gift_codes_collection = db['gift_codes']

# Initialize Amazon Polly
polly_client = boto3.client('polly', region_name=AWS_REGION, 
                            aws_access_key_id=AWS_ACCESS_KEY_ID,
                            aws_secret_access_key=AWS_SECRET_ACCESS_KEY)

# In-memory dictionaries to track user access times, generated images, and ongoing tasks
last_ai_use = {}
user_images = {}
user_tasks = {}

async def log_message(user, user_input, bot_response):
    if LOG_CHANNEL_ID:
        user_info = f"User ID: {user.id}\nUsername: @{user.username}\nName: {user.first_name} {user.last_name or ''}"
        message = f"{user_info}\nUser input: {user_input}\nBot response: {bot_response}"
        await bot.send_message(LOG_CHANNEL_ID, message)

def is_owner(user_id):
    return user_id == BOT_OWNER_ID

@dp.message_handler(commands=['start'])
async def start_command(message: types.Message):
    user = message.from_user
    user_input = message.text
    response_text = (
        'Hi! Send me a command /ai followed by your prompt to generate an image, or use /ask followed by your query to get an answer, '
        'or /dev to get developer info, or /help to get all commands info.\n\nDeveloped by @AkhandanandTripathi'
    )
    await message.reply(response_text)
    await log_message(user, user_input, response_text)
    full_name = f"{user.first_name} {user.last_name or ''}".strip()
    await users_collection.update_one(
        {'user_id': user.id},
        {'$set': {'username': user.username, 'full_name': full_name}},
        upsert=True
    )

@dp.message_handler(commands=['help'])
async def help_command(message: types.Message):
    user = message.from_user
    user_input = message.text
    response_text = (
        'Available commands:\n'
        '/start - Start the bot\n'
        '/ai <prompt> - Generate an image based on the prompt\n'
        '/proai <prompt> - Generate an image based on the prompt (professional, no time limit)\n'
        '/modify <prompt> - Modify the last generated image\n'
        '/ask <query> - Get an answer to your query\n'
        '/dev - Get developer info\n'
        '/setlogchannel <id> - Set the log channel (owner only)\n'
        '/ping - Check the server response time\n'
        '/generate - Generate a gift code (owner only)\n'
        '/redeem <code> - Redeem a gift code to get a professional plan\n'
        '/users - Get the list of users (owner only)\n'
        '/broadcast <message> - Broadcast a message to all users and groups (owner only)\n'
        '/cancelai - Cancel the current /ai command\n'
        '/cancelproai - Cancel the current /proai command\n'
        '/voice <text> - Convert text to speech using Amazon Polly'
    )
    await message.reply(response_text)
    await log_message(user, user_input, response_text)

@dp.message_handler(commands=['ai'])
async def ai_command(message: types.Message):
    user = message.from_user
    user_input = message.text
    user_id = user.id
    current_time = message.date

    if user_id in last_ai_use and (current_time - last_ai_use[user_id]).total_seconds() < 5:
        response_text = 'Please wait for 5 seconds before using the /ai command again.'
        await message.reply(response_text)
        await log_message(user, user_input, response_text)
        return

    last_ai_use[user_id] = current_time
    user_prompt = ' '.join(message.text.split(' ')[1:])
    if user_prompt:
        await message.reply('Generating image...')
        task = asyncio.create_task(generate_image_task(user, user_input, user_prompt))
        user_tasks[user_id] = task
    else:
        response_text = 'Please provide a prompt after the /ai command.'
        await message.reply(response_text)
        await log_message(user, user_input, response_text)

async def generate_image_task(user, user_input, prompt):
    image_url = generate_image(prompt)
    if image_url:
        await bot.send_photo(user.id, image_url)
        await log_message(user, user_input, image_url)
        user_images[user.id] = image_url
    else:
        response_text = 'Sorry, there was an error generating the image. Please contact @AkhandanandTripathi to fix it.'
        await bot.send_message(user.id, response_text)
        await log_message(user, user_input, response_text)

@dp.message_handler(commands=['proai'])
async def proai_command(message: types.Message):
    user = message.from_user
    user_input = message.text
    user_id = user.id

    subscription = await subscriptions_collection.find_one({'user_id': user_id})
    if subscription and subscription.get('plan') == 'professional':
        user_prompt = ' '.join(message.text.split(' ')[1:])
        if user_prompt:
            await message.reply('Generating images...')
            task = asyncio.create_task(generate_proai_task(user, user_input, user_prompt))
            user_tasks[user_id] = task
        else:
            response_text = 'Please provide a prompt after the /proai command.'
            await message.reply(response_text)
            await log_message(user, user_input, response_text)
    else:
        response_text = 'You need to redeem a gift code to use the /proai command.'
        await message.reply(response_text)
        await log_message(user, user_input, response_text)

async def generate_proai_task(user, user_input, prompt):
    for i in range(15):
        image_url = generate_image(f"{prompt} (image {i + 1} improving each time)")
        if image_url:
            await bot.send_photo(user.id, image_url)
            await log_message(user, user_input, image_url)
        else:
            response_text = 'Sorry, there was an error generating one of the images. Please contact @AkhandanandTripathi to fix it.'
            await bot.send_message(user.id, response_text)
            await log_message(user, user_input, response_text)
            break
        await asyncio.sleep(5)

@dp.message_handler(commands=['modify'])
async def modify_command(message: types.Message):
    user = message.from_user
    user_input = message.text
    user_id = user.id

    modify_prompt = ' '.join(message.text.split(' ')[1:])
    if message.reply_to_message and message.reply_to_message.photo:
        photo = message.reply_to_message.photo[-1]
        file_url = await bot.get_file_url(photo.file_id)
        if modify_prompt:
            await message.reply('Modifying the image...')
            modified_image_url = generate_image(f"Modify this image: {file_url} with {modify_prompt}")
            if modified_image_url:
                await message.reply_photo(modified_image_url)
                await log_message(user, user_input, modified_image_url)
            else:
                response_text = 'Sorry, there was an error modifying the image. Please contact @AkhandanandTripathi to fix it.'
                await message.reply(response_text)
                await log_message(user, user_input, response_text)
        else:
            response_text = 'Please provide a prompt after the /modify command.'
            await message.reply(response_text)
            await log_message(user, user_input, response_text)
    elif user_id in user_images:
        if modify_prompt:
            await message.reply('Modifying the last generated image...')
            modified_image_url = generate_image(f"Modify this image: {user_images[user_id]} with {modify_prompt}")
            if modified_image_url:
                await message.reply_photo(modified_image_url)
                await log_message(user, user_input, modified_image_url)
                user_images[user_id] = modified_image_url
            else:
                response_text = 'Sorry, there was an error modifying the image. Please contact @AkhandanandTripathi to fix it.'
                await message.reply(response_text)
                await log_message(user, user_input, response_text)
        else:
            response_text = 'Please provide a prompt after the /modify command.'
            await message.reply(response_text)
            await log_message(user, user_input, response_text)
    else:
        response_text = 'No image found to modify. Please generate an image first using /ai command or reply to an image with /modify command.'
        await message.reply(response_text)
        await log_message(user, user_input, response_text)

@dp.message_handler(commands=['ask'])
async def ask_command(message: types.Message):
    user = message.from_user
    user_input = message.text
    user_question = ' '.join(message.text.split(' ')[1:])
    if user_question:
        await message.reply('Thinking...')
        answer = generate_image(user_question)
        if answer:
            await message.reply(answer)
            await log_message(user, user_input, answer)
        else:
            response_text = 'Sorry, there was an error generating the answer. Please contact @AkhandanandTripathi to fix it.'
            await message.reply(response_text)
            await log_message(user, user_input, response_text)
    else:
        response_text = 'Please provide a query after the /ask command.'
        await message.reply(response_text)
        await log_message(user, user_input, response_text)

@dp.message_handler(commands=['dev'])
async def dev_command(message: types.Message):
    user = message.from_user
    user_input = message.text
    response_text = 'Developer @AkhandanandTripathi'
    await message.reply(response_text)
    await log_message(user, user_input, response_text)

@dp.message_handler(commands=['setlogchannel'])
async def setlogchannel_command(message: types.Message):
    user = message.from_user
    user_input = message.text
    if is_owner(user.id):
        args = message.text.split(' ')[1:]
        if len(args) == 1:
            global LOG_CHANNEL_ID
            LOG_CHANNEL_ID = args[0]
            response_text = f"Log channel set to {LOG_CHANNEL_ID}"
            await message.reply(response_text)
            await log_message(user, user_input, response_text)
        else:
            response_text = 'Usage: /setlogchannel <log_channel_id>'
            await message.reply(response_text)
            await log_message(user, user_input, response_text)
    else:
        response_text = "You don't have permission to use this command. Please ask @AkhandanandTripathi to do it."
        await message.reply(response_text)
        await log_message(user, user_input, response_text)

@dp.message_handler(commands=['ping'])
async def ping_command(message: types.Message):
    user = message.from_user
    user_input = message.text
    start_time = message.date.timestamp()
    await message.reply('Pong!')
    end_time = message.date.timestamp()
    ping_time = int((end_time - start_time) * 1000)
    response_text = f'Pong! {ping_time} ms'
    await message.reply(response_text)
    await log_message(user, user_input, response_text)

@dp.message_handler(commands=['generate'])
async def generate_command(message: types.Message):
    user = message.from_user
    user_input = message.text
    if is_owner(user.id):
        code = generate_code()
        await gift_codes_collection.insert_one({'code': code, 'plan': 'professional'})
        response_text = f"Generated gift code: {code}"
        await message.reply(response_text)
        await log_message(user, user_input, response_text)
    else:
        response_text = "You don't have permission to use this command. Please ask @AkhandanandTripathi to do it."
        await message.reply(response_text)
        await log_message(user, user_input, response_text)

@dp.message_handler(commands=['redeem'])
async def redeem_command(message: types.Message):
    user = message.from_user
    user_input = message.text
    args = message.text.split(' ')[1:]
    if len(args) == 1:
        code = args[0]
        gift_code = await gift_codes_collection.find_one_and_delete({'code': code})
        if gift_code:
            await subscriptions_collection.update_one({'user_id': user.id}, {'$set': {'plan': 'professional'}}, upsert=True)
            response_text = "You have successfully redeemed the code and upgraded to the professional plan."
            await message.reply(response_text)
            await log_message(user, user_input, response_text)
        else:
            response_text = "Invalid or already redeemed gift code."
            await message.reply(response_text)
            await log_message(user, user_input, response_text)
    else:
        response_text = "Usage: /redeem <gift_code>"
        await message.reply(response_text)
        await log_message(user, user_input, response_text)

@dp.message_handler(commands=['users'])
async def users_command(message: types.Message):
    user = message.from_user
    user_input = message.text
    if is_owner(user.id):
        users = await users_collection.find().to_list(None)
        for user_data in users:
            user_id = user_data['user_id']
            username = user_data['username']
            full_name = user_data['full_name']
            user_info = (
                f"<b>User ID:</b> {user_id}\n"
                f"<b>Username:</b> @{username}\n"
                f"<b>Name:</b> {full_name}\n"
                f"<b>Permanent Link:</b> <a href='tg://user?id={user_id}'>Open Chat</a>"
            )
            await message.reply(user_info, parse_mode=ParseMode.HTML)
            await log_message(user, user_input, user_info)
    else:
        response_text = "Ummmm, you are not capable of it."
        await message.reply(response_text)
        await log_message(user, user_input, response_text)

@dp.message_handler(commands=['broadcast'])
async def broadcast_command(message: types.Message):
    user = message.from_user
    user_input = message.text
    if is_owner(user.id):
        broadcast_message = ' '.join(message.text.split(' ')[1:])
        if broadcast_message:
            users = await users_collection.find().to_list(None)
            for user_data in users:
                try:
                    await bot.send_message(user_data['user_id'], broadcast_message)
                except Exception as e:
                    log.error(f"Error sending message to {user_data['user_id']}: {e}")
            groups = await groups_collection.find().to_list(None)
            for group_data in groups:
                try:
                    await bot.send_message(group_data['group_id'], broadcast_message)
                except Exception as e:
                    log.error(f"Error sending message to {group_data['group_id']}: {e}")
            response_text = "Broadcast message sent."
            await message.reply(response_text)
            await log_message(user, user_input, response_text)
        else:
            response_text = "Usage: /broadcast <message>"
            await message.reply(response_text)
            await log_message(user, user_input, response_text)
    else:
        response_text = "Ummmm, you are not capable of it."
        await message.reply(response_text)
        await log_message(user, user_input, response_text)

@dp.message_handler(commands=['cancelai'])
async def cancelai_command(message: types.Message):
    user = message.from_user
    user_id = user.id
    if user_id in user_tasks and not user_tasks[user_id].done():
        user_tasks[user_id].cancel()
        await message.reply('Your /ai command has been canceled.')
    else:
        await message.reply('You do not have any active /ai command.')

@dp.message_handler(commands=['cancelproai'])
async def cancelproai_command(message: types.Message):
    user = message.from_user
    user_id = user.id
    if user_id in user_tasks and not user_tasks[user_id].done():
        user_tasks[user_id].cancel()
        await message.reply('Your /proai command has been canceled.')
    else:
        await message.reply('You do not have any active /proai command.')

@dp.message_handler(commands=['voice'])
async def voice_command(message: types.Message):
    user = message.from_user
    user_input = message.text
    text_to_speech = ' '.join(message.text.split(' ')[1:])
    if text_to_speech:
        try:
            response = polly_client.synthesize_speech(
                Text=text_to_speech,
                OutputFormat='mp3',
                VoiceId='Joanna'
            )
            audio_stream = response['AudioStream'].read()
            with open("akhandanandtripathi.mp3", 'wb') as file:
                file.write(audio_stream)
            with open("akhandanandtripathi.mp3", 'rb') as file:
                await bot.send_audio(user.id, file, title="akhandanandtripathi")
            await log_message(user, user_input, "Audio generated and sent.")
        except Exception as e:
            log.error(f"Error generating voice: {e}")
            response_text = 'Sorry, there was an error generating the audio. Please contact @AkhandanandTripathi to fix it.'
            await message.reply(response_text)
            await log_message(user, user_input, response_text)
    else:
        response_text = 'Please provide text to convert to speech after the /voice command.'
        await message.reply(response_text)
        await log_message(user, user_input, response_text)

@dp.message_handler(content_types=['new_chat_members'])
async def new_chat_members_handler(message: types.Message):
    chat = message.chat
    if chat.type in ['group', 'supergroup']:
        await groups_collection.update_one({'group_id': chat.id}, {'$set': {'title': chat.title}}, upsert=True)
        log.info(f"Added to group: {chat.title}")

if __name__ == '__main__':
    from aiogram import executor
    import asyncio

    async def on_startup(dp):
        log.info('Bot started')

    async def on_shutdown(dp):
        await bot.close()
        log.info('Bot stopped')

    executor.start_polling(dp, on_startup=on_startup, on_shutdown=on_shutdown)
