from fastapi import FastAPI, File, Request, BackgroundTasks, HTTPException

import time
import os
import uuid
import re
import requests
from supabase import create_client, Client
from pydub import AudioSegment
from linebot import (
    LineBotApi, WebhookHandler, WebhookParser
)
from linebot.exceptions import (
    InvalidSignatureError
)

from linebot.models import (
    MessageEvent, FileMessage, TextSendMessage, AudioMessage
)
import openai

from errors import TranscriptionFailureError, FileSizeError, FileExtensionError, FileCorruptionError, UsageLimitError

LINEAPI_ACCESS_TOKEN = os.getenv("LINEAPI_ACCESS_TOKEN")
LINEAPI_SECRET = os.getenv("LINEAPI_SECRET")
line_bot_api = LineBotApi(LINEAPI_ACCESS_TOKEN)
parser = WebhookParser(LINEAPI_SECRET)

openai.organization = os.getenv("OPENAI_ORGANIZATION")
openai.api_key = os.getenv("OPENAI_API_KEY")
url: str = os.getenv("SUPABASE_URL")
key: str = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(url, key)
app = FastAPI()
handler = WebhookHandler(LINEAPI_SECRET)

ACCEPT_FILE_EXTENSIONS = ["m4a", "mp3", "mp4", "mpeg", "mpga", "wav", "webm"]
LIMITATION_SEC = 180

@app.post("/callback")
async def handle_request(request: Request):
    body = (await request.body()).decode("utf-8")
    signature = request.headers.get("X-Line-Signature", "")
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature. Please check your channel access token/channel secret.")

@app.get("/health")
async def health():
    return "ok"

@handler.add(MessageEvent, message=FileMessage)
def handle_audio_file(event):
    message_id = event.message.id
    message_content = line_bot_api.get_message_content(message_id)
    handle_message_content(event, message_content)

@handler.add(MessageEvent, message=AudioMessage)
def handle_audio_message(event):
    message_id = event.message.id
    CONTENT_READY_URL = f"https://api-data.line.me/v2/bot/message/{message_id}/content/transcoding"
    headers = {'Authorization': f'Bearer {LINEAPI_ACCESS_TOKEN}'}

    status = requests.get(CONTENT_READY_URL, headers=headers).json().get("status", "")
    while status == "processing":
        time.sleep(1)
        status = requests.get(CONTENT_READY_URL, headers=headers).json().get("status", "")
    if status == "succeeded":
        message_content = line_bot_api.get_message_content(message_id)
        handle_message_content(event, message_content)
    else:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="音声ファイルの取得に失敗しちゃいました・・・"))


@handler.default()
def default(event):
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text="音声ファイルを送信してね！"))

def handle_message_content(event, message_content):
    matched_extension = re.search(r'\b(?:%s)\b' % '|'.join(ACCEPT_FILE_EXTENSIONS), message_content.content_type)
    if matched_extension:
        file_id = event.message.id
        matched_extension = matched_extension.group(0)
        audio_file_path = f'/audio/{file_id}.{matched_extension}'
        with open(audio_file_path, 'wb') as fd:
            # TODO: ここでファイルサイズをチェックし、大きすぎる場合はエラーを返す
            # TODO: ファイル書き込みを行わずに、直接openai.Audio.transcribeに渡す -> メモリを考慮するとできなそう
            for chunk in message_content.iter_content():
                fd.write(chunk)
        try:
            user_id = event.source.user_id
            text = transcribe(audio_file_path, matched_extension, user_id)
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=text))
        except TranscriptionFailureError:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="書き起こしに失敗しちゃいました・・・もう一度お試しください！"))
        except FileSizeError:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="ファイルサイズが大きすぎるみたいです・・・"))
        except FileCorruptionError:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="ファイルが壊れているみたいです・・・"))
        except UsageLimitError as e:
            if e.remaining_sec < 60:
                remaining_time_text = str(int(e.remaining_sec)) + "秒"
            else:
                remaining_time_text = str(e.remaining_sec // 60) + "分"
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="利用制限時間を超えちゃうみたいです・・・\n残りの利用可能時間は" + remaining_time_text + "です！"))
        except Exception as e:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="予期せぬエラーが発生しました・・・"))
        finally:
            os.remove(audio_file_path)
    else:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="対応していないファイル形式です・・・\n以下のファイル形式で試してみてね!\n" + ", ".join(ACCEPT_FILE_EXTENSIONS)))


def transcribe(audio_file_path, extension, user_id):
    try:
        audio = AudioSegment.from_file(audio_file_path, extension)
    except Exception as e:
        raise FileCorruptionError
    duration_sec = audio.duration_seconds
    data = supabase.table('usage_counter').select('usage_sec').filter('user_id', 'eq', user_id).execute().data
    if len(data) == 0:
        usage_sec = duration_sec
    else:
        usage_sec = data[0]['usage_sec'] + duration_sec
    if usage_sec > LIMITATION_SEC:
        raise UsageLimitError(remaining_sec=LIMITATION_SEC - data[0]['usage_sec'])
    supabase.table('usage_counter').upsert({'user_id': user_id, 'usage_sec': usage_sec}).execute()
    audio_file= open(audio_file_path, "rb")
    try:
        transcript = openai.Audio.transcribe("whisper-1", audio_file)
        text = transcript.get("text", "")
        return text
    except Exception as e:
        raise TranscriptionFailureError
    finally:
        audio_file.close()