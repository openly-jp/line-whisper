from fastapi import APIRouter, HTTPException, Request
from linebot import (
    LineBotApi, WebhookHandler, WebhookParser
)
import requests
import time
import math
from supabase import create_client
import openai
from linebot.exceptions import (
    InvalidSignatureError
)
from pydub import AudioSegment
from linebot.models import (
    MessageEvent, FileMessage, TextSendMessage, AudioMessage, VideoMessage, FlexSendMessage
)
import os
import ffmpeg
import srt
from tempfile import NamedTemporaryFile
from .errors import TranscriptionFailureError, FileSizeError, FileExtensionError, FileCorruptionError, UsageLimitError, TranscriptionTimeoutError
import logging
import threading, queue

# Logging related
logger = logging.getLogger("line-logger")
handler = logging.FileHandler("../../logs/line.log")
handler.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)

LINEAPI_ACCESS_TOKEN = os.getenv("LINEAPI_ACCESS_TOKEN")
LINEAPI_SECRET = os.getenv("LINEAPI_SECRET")
line_bot_api_client = LineBotApi(LINEAPI_ACCESS_TOKEN)
parser = WebhookParser(LINEAPI_SECRET)
line_handler = WebhookHandler(LINEAPI_SECRET)

openai.organization = os.getenv("OPENAI_ORGANIZATION")
openai.api_key = os.getenv("OPENAI_API_KEY")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)

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

LIMITATION_FILE_SIZE_MB = int(os.getenv("LIMITATION_FILE_SIZE_MB"))
LIMITATION_FILE_SIZE = LIMITATION_FILE_SIZE_MB * 1024 * 1024

router = APIRouter(
    prefix="/line",
    tags=["line"],
    responses={404: {"description": "Not found"}},
)

@router.post("/callback")
async def callback(request: Request):
    body = (await request.body()).decode("utf-8")
    signature = request.headers.get("X-Line-Signature", "")
    try:
        line_handler.handle(body, signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature. Please check your channel access token/channel secret.")

@line_handler.add(MessageEvent, message=FileMessage)
def handle_audio_file(event):
    message_id = event.message.id
    message_content = line_bot_api_client.get_message_content(message_id)
    handle_message_content(event, message_content)

@line_handler.add(MessageEvent, message=AudioMessage)
def handle_audio_message(event):
    message_id = event.message.id
    CONTENT_READY_URL = f"https://api-data.line.me/v2/bot/message/{message_id}/content/transcoding"
    headers = {'Authorization': f'Bearer {LINEAPI_ACCESS_TOKEN}'}

    status = requests.get(CONTENT_READY_URL, headers=headers).json().get("status", "")
    while status == "processing":
        time.sleep(1)
        status = requests.get(CONTENT_READY_URL, headers=headers).json().get("status", "")
    if status == "succeeded":
        message_content = line_bot_api_client.get_message_content(message_id)
        handle_message_content(event, message_content)
    else:
        line_bot_api_client.reply_message(
            event.reply_token,
            TextSendMessage(text="音声ファイルの取得に失敗したのじゃ・・・"))

@line_handler.add(MessageEvent, message=VideoMessage)
def handle_video_message(event):
    message_id = event.message.id
    CONTENT_READY_URL = f"https://api-data.line.me/v2/bot/message/{message_id}/content/transcoding"
    headers = {'Authorization': f'Bearer {LINEAPI_ACCESS_TOKEN}'}

    status = requests.get(CONTENT_READY_URL, headers=headers).json().get("status", "")
    while status == "processing":
        time.sleep(1)
        status = requests.get(CONTENT_READY_URL, headers=headers).json().get("status", "")
    if status == "succeeded":
        message_content = line_bot_api_client.get_message_content(message_id)
        handle_message_content(event, message_content)
    else:
        line_bot_api_client.reply_message(
            event.reply_token,
            TextSendMessage(text="動画ファイルの取得に失敗したのじゃ・・・"))


@line_handler.default()
def default(event):
    line_bot_api_client.reply_message(
        event.reply_token,
        TextSendMessage(text="音声ファイルを送信するのじゃ！"))


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
                    line_bot_api_client.reply_message(
                        event.reply_token,
                        TextSendMessage(text=f"ファイルサイズが大きすぎるようじゃ・・・\n{LIMITATION_FILE_SIZE_MB}MB以下のファイルを送信するのじゃ！"))
                    return
                fd.write(chunk)
                total_file_size += len(chunk)
        try:
            user_id = event.source.user_id
            text, additional_comment = transcribe(audio_file_path, matched_extension, user_id)
            text = text + "\n\n" + additional_comment if additional_comment else text
            text_len = len(text)
            if text_len < 5000:
                line_bot_api_client.reply_message(
                    event.reply_token,
                    TextSendMessage(text=text))
            else:
                text_start_index = 0
                processed_text_len = 0
                count = 0
                while processed_text_len < text_len:
                    text_end_index = min(text_start_index + 5000, text_len)
                    if count == 0:
                        line_bot_api_client.reply_message(
                            event.reply_token,
                            TextSendMessage(text=text[text_start_index:text_end_index]))
                    else:
                        line_bot_api_client.push_message(
                            user_id,
                            TextSendMessage(text=text[text_start_index:text_end_index]))
                    text_start_index = text_end_index
                    processed_text_len += 5000
                    count += 1
            if additional_comment is not None:
                line_bot_api_client.push_message(
                    user_id,
                    get_payment_promotion_message()
                )
        except TranscriptionFailureError as e:
            line_bot_api_client.reply_message(
                event.reply_token,
                TextSendMessage(text="文字起こしに失敗したのじゃ・・・もう一度お試しくだされ！"))
        except FileSizeError:
            line_bot_api_client.reply_message(
                event.reply_token,
                TextSendMessage(text=f"ファイルサイズが大きすぎるみたいじゃ・・・\n{LIMITATION_FILE_SIZE_MB}MB以下のファイルを送信するのじゃ！"))
        except FileCorruptionError:
            line_bot_api_client.reply_message(
                event.reply_token,
                TextSendMessage(text="ファイルが壊れているみたいじゃ・・・"))
        except UsageLimitError as e:
            line_bot_api_client.reply_message(
                event.reply_token,
                get_payment_promotion_message(e.required_sec)
            )
        except TranscriptionTimeoutError as e:
            line_bot_api_client.reply_message(
                event.reply_token,
                TextSendMessage(text=f"API呼び出しがタイムアウトしたのじゃ・・・再度お試しくだされ！"))
        except Exception as e:
            line_bot_api_client.reply_message(
                event.reply_token,
                TextSendMessage(text="予期せぬエラーが発生したのじゃ・・・"))
        finally:
            os.remove(audio_file_path)
    else:
        line_bot_api_client.reply_message(
            event.reply_token,
            TextSendMessage(text="対応していないファイル形式のようじゃの・・・\n以下のファイル形式で試すのじゃ!\n" + ", ".join(ACCEPT_FILE_EXTENSIONS)))


def get_chunk_audio_file(audio_file_path, format, start_msec, duration_msec):
    with NamedTemporaryFile("w+b", suffix=f".{format}") as f:
        ffmpeg_cmd = (
            ffmpeg
            .input(audio_file_path, ss=start_msec / 1000, t=duration_msec / 1000)
            .output(f.name, acodec='copy')
            .overwrite_output()
        )
        ffmpeg_cmd.run(quiet=True)
        audio_file = open(f.name, "rb")
    return audio_file

def get_audio_duration_msec(audio_file_path):
    # NOTE: By using ffmpeg, hopefully we can reduce the memory usage
    result = ffmpeg.probe(audio_file_path)
    duration = float(result['format']['duration'])
    return duration * 1000

def transcribe(audio_file_path, extension, user_id):
    try:
        audio_duration_msec = get_audio_duration_msec(audio_file_path)
    except Exception as e:
        raise FileCorruptionError
    result_text = ""
    additional_comment = None

    data = supabase_client.table('user_info').select('remaining_sec').filter('id', 'eq', user_id).execute().data
    if len(data) == 0:
        # create new user first
        supabase_client.table('user_info').insert({'id': user_id, 'remaining_sec': 300}).execute()
        data = supabase_client.table('user_info').select('remaining_sec').filter('id', 'eq', user_id).execute().data

    old_remaining_sec = data[0]['remaining_sec']
    new_remaining_sec = old_remaining_sec - audio_duration_msec / 1000

    # check if recognition is possible
    if old_remaining_sec < 1:
        raise UsageLimitError(required_sec=audio_duration_msec / 1000)
    else:
        # to avoid data competition, update remaining_sec ASAP
        supabase_client.table('user_info').upsert({'id': user_id, 'remaining_sec': max(new_remaining_sec, 0)}).execute()
    # check if audio cut is necessary
    if new_remaining_sec < 0:
        old_audio_duration_msec = audio_duration_msec
        audio_duration_msec = old_remaining_sec * 1000
        additional_comment = "利用制限時間を超えたようじゃ、冒頭の" + get_remaining_time_text(old_remaining_sec) + "だけ書き起こしたぞ！\n" + f"{get_remaining_time_text(old_audio_duration_msec / 1000)}の文字起こし時間が必要じゃ！"

    CHUNK_DURATION_MSEC = 10 * 60 * 1000

    start_msec = 0
    processed_duration_msec = 0

    def call_openai_api(audio_file_path, format, start_msec, duration_msec, t_queue):
        chunk_audio_file = get_chunk_audio_file(audio_file_path, format, start_msec, duration_msec)
        try:
            result = openai.Audio.transcribe("whisper-1", chunk_audio_file, language="ja", response_format="srt")
            t_queue.put(result)
        except Exception as e:
            t_queue.put(e)
        finally:
            chunk_audio_file.close()

    while processed_duration_msec < audio_duration_msec:
        duration_msec = min(CHUNK_DURATION_MSEC, audio_duration_msec - processed_duration_msec)
        try:
            # TODO: 以下の部分を非同期に行うことで他のユーザーのリクエストを処理できるようにする
            #with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            #    executor_result = executor.submit(call_openai_api, (chunk_audio_file.read()))
            #    chunk_result = executor_result.result(timeout=10)
            t_queue = queue.Queue()
            t = threading.Thread(target=call_openai_api, args=(audio_file_path, extension, start_msec, duration_msec, t_queue,))
            t.start()
            t.join(timeout=int(os.getenv("TIMEOUT_SEC")))
            if t.is_alive():
                raise TimeoutError
            t_result = t_queue.get()
            if isinstance(t_result, Exception):
                raise t_result
            else:
                chunk_result = t_result
            srt_rows = srt.parse(chunk_result)
            for i, row in enumerate(srt_rows):
                text = row.content.strip()
                if text == "":
                    continue
                if i == 0 and start_msec == 0:
                    result_text += text
                else:
                    result_text += ("\n" + text)
            processed_duration_msec += duration_msec
            start_msec += duration_msec
        except TimeoutError:
            supabase_client.table('user_info').upsert({'id': user_id, 'remaining_sec': old_remaining_sec}).execute()
            raise TranscriptionTimeoutError
        except Exception as e:
            logger.error(e)
            supabase_client.table('user_info').upsert({'id': user_id, 'remaining_sec': old_remaining_sec}).execute()
            raise TranscriptionFailureError

    return result_text, additional_comment



def get_remaining_time_text(remaining_sec):
    if remaining_sec < 60:
        remaining_time_text = str(int(remaining_sec)) + "秒"
    else:
        remaining_time_text = str(math.ceil(remaining_sec / 60)) + "分"
    return remaining_time_text

def get_payment_promotion_message(required_sec=None):
    return  FlexSendMessage(
                alt_text='追加の文字起こし時間を購入するのじゃ！',
                contents={
                    "type": "bubble",
                    "body": {
                        "type": "box",
                        "layout": "vertical",
                        "contents": [
                        {
                            "type": "text",
                            "text": (f"{get_remaining_time_text(required_sec)}の文字起こし時間が必要じゃ！\n" if required_sec else "") + "60分120円から追加の文字起こしができるぞい！",
                            "wrap": True
                        }
                        ]
                    },
                    "footer": {
                        "type": "box",
                        "layout": "vertical",
                        "spacing": "sm",
                        "contents": [
                        {
                            "type": "button",
                            "style": "link",
                            "height": "sm",
                            "action": {
                            "type": "uri",
                            "label": "購入ページへ",
                            "uri": os.getenv("PAYMENT_PAGE_URL")
                            },
                            "color": "#FFFFFF"
                        }
                        ],
                        "flex": 0,
                        "backgroundColor": "#06c755"
                    },
                     "action": {
                        "type": "uri",
                        "label": "action",
                        "uri": os.getenv("PAYMENT_PAGE_URL")
                    }
                }
            )
