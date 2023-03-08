from fastapi import FastAPI, Request, HTTPException

import time
import math
import os
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
    MessageEvent, FileMessage, TextSendMessage, AudioMessage, VideoMessage
)
import openai

from errors import TranscriptionFailureError, FileSizeError, FileExtensionError, FileCorruptionError, UsageLimitError

LINEAPI_ACCESS_TOKEN = os.getenv("LINEAPI_ACCESS_TOKEN")
LINEAPI_SECRET = os.getenv("LINEAPI_SECRET")
line_bot_api = LineBotApi(LINEAPI_ACCESS_TOKEN)
parser = WebhookParser(LINEAPI_SECRET)
handler = WebhookHandler(LINEAPI_SECRET)

openai.organization = os.getenv("OPENAI_ORGANIZATION")
openai.api_key = os.getenv("OPENAI_API_KEY")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

app = FastAPI()

ACCEPT_FILE_EXTENSIONS = ["m4a", "mp3", "mp4", "wav"]
CONTENT_TYPE_EXTENSION_MAP = {
    "audio/aac": "m4a",
    "audio/x-m4a": "m4a",
    "audio/m4a": "m4a",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/mpeg3": "mp3",
    "video/mp4": "mp4",
    "video/mpeg4": "mp4",
    "audio/wav": "wav",
}
LIMITATION_SEC = int(os.getenv("LIMITATION_SEC"))
LIMITATION_FILE_SIZE_MB = int(os.getenv("LIMITATION_FILE_SIZE_MB"))
LIMITATION_FILE_SIZE = LIMITATION_FILE_SIZE_MB * 1024 * 1024

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
            TextSendMessage(text="音声ファイルの取得に失敗したのじゃ・・・"))

@handler.add(MessageEvent, message=VideoMessage)
def handle_video_message(event):
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
            TextSendMessage(text="動画ファイルの取得に失敗したのじゃ・・・"))


@handler.default()
def default(event):
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text="音声ファイルを送信してね！"))

def handle_message_content(event, message_content):
    #matched_extension = re.search(r'\b(?:%s)\b' % '|'.join(ACCEPT_FILE_EXTENSIONS), message_content.content_type)
    matched_extension = CONTENT_TYPE_EXTENSION_MAP.get(message_content.content_type, "")
    if matched_extension:
        file_id = event.message.id
        audio_file_path = f'/audio/{file_id}.{matched_extension}'
        total_file_size = 0
        with open(audio_file_path, 'wb') as fd:
            # TODO: ファイル書き込みを行わずに、直接openai.Audio.transcribeに渡す
            for chunk in message_content.iter_content():
                # check file size is under 25MB
                if total_file_size > LIMITATION_FILE_SIZE:
                    line_bot_api.reply_message(
                        event.reply_token,
                        TextSendMessage(text=f"ファイルサイズが大きすぎるようじゃ・・・\n{LIMITATION_FILE_SIZE_MB}MB以下のファイルを送信するのじゃ！"))
                    return
                fd.write(chunk)
                total_file_size += len(chunk)
        try:
            user_id = event.source.user_id
            text, additional_comment = transcribe(audio_file_path, matched_extension, user_id)
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=text + "\n\n" + additional_comment if additional_comment else text))
        except TranscriptionFailureError:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="書き起こしに失敗したのじゃ・・・もう一度お試しくだされ！"))
        except FileSizeError:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"ファイルサイズが大きすぎるみたいじゃ・・・\n{LIMITATION_FILE_SIZE_MB}MB以下のファイルを送信するのじゃ！"))
        except FileCorruptionError:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="ファイルが壊れているみたいじゃ・・・"))
        except UsageLimitError as e:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="利用制限時間を超えちゃうようじゃ・・・\n残りの利用可能時間は" + get_remaining_time_text(e.remaining_sec) + "らしいぞ！"))
        except Exception as e:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="予期せぬエラーが発生したのじゃ・・・"))
        finally:
            os.remove(audio_file_path)
    else:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="対応していないファイル形式のようじゃの・・・\n以下のファイル形式で試すのじゃ!\n" + ", ".join(ACCEPT_FILE_EXTENSIONS)))


def transcribe(audio_file_path, extension, user_id):
    try:
        audio = AudioSegment.from_file(audio_file_path, extension)
    except Exception as e:
        raise FileCorruptionError
    additional_comment = None
    duration_sec = audio.duration_seconds
    data = supabase.table('user_info').select('usage_sec').filter('id', 'eq', user_id).execute().data
    if len(data) == 0:
        usage_sec = duration_sec
    else:
        usage_sec = data[0]['usage_sec'] + duration_sec
    if usage_sec > LIMITATION_SEC:
        remaining_sec = LIMITATION_SEC - data[0]['usage_sec']
        if remaining_sec < 1:
            raise UsageLimitError(remaining_sec=0)
        else:
            # transcribe only remaining time
            # PyDub handles time in milliseconds
            audio = audio[:math.floor(remaining_sec * 1000)]
            # PyDub cannot export m4a file so convert it to mp3
            audio.export(audio_file_path, format=extension if extension != "m4a" else "mp3")
            usage_sec = data[0]['usage_sec'] + remaining_sec
            additional_comment = "利用制限時間を超えたようじゃ、冒頭の" + get_remaining_time_text(remaining_sec) + "だけ書き起こしたぞ！"
    audio_file= open(audio_file_path, "rb")
    try:
        # TODO: 以下の部分を非同期に行うことで他のユーザーのリクエストを処理できるようにする
        transcript = openai.Audio.transcribe("whisper-1", audio_file, language="ja")
        text = transcript.get("text", "")
        supabase.table('user_info').upsert({'id': user_id, 'usage_sec': usage_sec}).execute()
        return text, additional_comment
    except Exception as e:
        raise TranscriptionFailureError
    finally:
        audio_file.close()

def get_remaining_time_text(remaining_sec):
    if remaining_sec < 60:
        remaining_time_text = str(int(remaining_sec)) + "秒"
    else:
        remaining_time_text = str(int(remaining_sec // 60)) + "分"
    return remaining_time_text