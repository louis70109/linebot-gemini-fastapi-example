import logging
import os
import re
import sys
if os.getenv('API_ENV') != 'production':
    from dotenv import load_dotenv

    load_dotenv()


from fastapi import FastAPI, HTTPException, Request

from linebot.v3.webhook import WebhookParser
from linebot.v3.messaging import (
    AsyncApiClient,
    AsyncMessagingApi,
    Configuration,
    ReplyMessageRequest,
    TextMessage)
from linebot.v3.exceptions import (
    InvalidSignatureError
)
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent,
)

import uvicorn
import json
from datetime import datetime, timedelta
import urllib
import google.generativeai as genai

logging.basicConfig(level=os.getenv('LOG', 'WARNING'))
logger = logging.getLogger(__file__)

app = FastAPI()

channel_secret = os.getenv('LINE_CHANNEL_SECRET', None)
channel_access_token = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', None)
if channel_secret is None:
    print('Specify LINE_CHANNEL_SECRET as environment variable.')
    sys.exit(1)
if channel_access_token is None:
    print('Specify LINE_CHANNEL_ACCESS_TOKEN as environment variable.')
    sys.exit(1)

configuration = Configuration(
    access_token=channel_access_token
)

async_api_client = AsyncApiClient(configuration)
line_bot_api = AsyncMessagingApi(async_api_client)
parser = WebhookParser(channel_secret)


firebase_url = os.getenv('FIREBASE_URL')
gemini_key = os.getenv('GEMINI_API_KEY')


# Initialize the Gemini Pro API
genai.configure(api_key=gemini_key)


@app.get("/health")
async def health():
    return 'ok'


def split_gcal_time_utc(cal_date):
    if "/" in cal_date:
        start_time_str, end_time_str = cal_date.split("/")
        start_time = datetime.strptime(start_time_str, "%Y%m%dT%H%M%S%z")
        end_time = datetime.strptime(end_time_str, "%Y%m%dT%H%M%S%z")
        utc_plus_8 = timedelta(hours=8)
        start_time_adjusted = start_time - utc_plus_8
        end_time_adjusted = end_time - utc_plus_8
        logging.debug("-------start '/'-----------")
        logging.debug(start_time_adjusted)
        logging.debug(str(start_time_adjusted.strftime("%Y%m%dT%H%M%S") + "Z"))
        logging.debug(end_time_adjusted)
        logging.debug(str(end_time_adjusted.strftime("%Y%m%dT%H%M%S") + "Z"))
        logging.debug("-------end '/'-----------")

        return str(start_time_adjusted.strftime("%Y%m%dT%H%M%S") + "Z")+'/'+str(end_time_adjusted.strftime("%Y%m%dT%H%M%S") + "Z")
    else:
        start_time = datetime.strptime(cal_date, "%Y%m%dT%H%M%S%z")
        end_time = datetime.strptime(cal_date, "%Y%m%dT%H%M%S%z")
        utc_plus_8 = timedelta(hours=8)
        start_time_adjusted = start_time - timedelta(hours=8)
        end_time_adjusted = end_time - timedelta(hours=7)
        logging.debug("----------no '/'--------------")
        logging.debug(start_time_adjusted)
        logging.debug(str(start_time_adjusted.strftime("%Y%m%dT%H%M%S") + "Z"))
        logging.debug(end_time_adjusted)
        logging.debug(str(end_time_adjusted.strftime("%Y%m%dT%H%M%S") + "Z"))
        logging.debug("----------no '/'--------------")

        return str(start_time_adjusted.strftime("%Y%m%dT%H%M%S") + "Z")+'/'+str(end_time_adjusted.strftime("%Y%m%dT%H%M%S") + "Z")


def create_gcal_url(
        title='看到這個..請重生',
        date='20230524T180000/20230524T220000',
        location='那邊',
        description='TBC'):
    base_url = "https://www.google.com/calendar/render?action=TEMPLATE"
    event_url = f"{base_url}&text={urllib.parse.quote(title)}&dates={date}&location={urllib.parse.quote(location)}&details={urllib.parse.quote(description)}"
    return event_url+"&openExternalBrowser=1"


@app.post("/webhooks/line")
async def handle_callback(request: Request):
    signature = request.headers['X-Line-Signature']

    # get request body as text
    body = await request.body()
    body = body.decode()

    try:
        events = parser.parse(body, signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    for event in events:
        logging.info(event)
        if not isinstance(event, MessageEvent):
            continue
        if not isinstance(event.message, TextMessageContent):
            continue
        text = event.message.text

        if event.message.type == 'text':
            try:
                model = genai.GenerativeModel('gemini-pro')
                prompt = 'please arrange following user text, give me a json and contain title, description, locations(array) and dates(array) (example: 20210407T180000Z/20210407T190000Z). current time is '+datetime.now()+', use ZH-TW. json example = `{ "title": "去台大彈吉他", "description": "TBC", "locations": ["台大"], "dates": ["20240409T150000Z"]}\n'+text

                response = model.generate_content(prompt)
                logging.debug(response.text)
                # 去除 markdown 格式

                text_string = re.sub(
                    r"(```|\*\*|~|\(.*?\)|`.*?`|_|\n|JSON|json)", "", response.text)
                logging.debug('JSON string: '+str(text_string))
                # 將 text_string 轉換成字典
                data = json.loads(text_string)
                title = data["title"]
                description = data["description"] if data["description"] is not None else 'TBC'
                location = data["locations"][0] if len(
                    data["locations"]) > 0 else 'TBC'
                dates = data["dates"][0]

                text = create_gcal_url(
                    title, split_gcal_time_utc(dates), location, description)
            except Exception as e:
                logging.warning('Gemini 解析失敗\n'+e)
                continue
        await line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=text)]
            )
        )
    return 'OK'

if __name__ == "__main__":
    port = int(os.environ.get('PORT', default=8000))
    debug = True if os.environ.get(
        'API_ENV', default='develop') == 'develop' else False
    logging.info('Application will start...')
    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=debug)
