# -- coding: utf-8 --
import os, time, base64, asyncio
import pyaudio
import queue
import threading
from enum import Enum
import json
import websockets
from typing import Optional, Callable, Dict, Any

# 在这里硬编码API密钥
DASHSCOPE_API_KEY = "sk-4790da8d470049e9be356a6c0179e5b9"

# 创建一个全局音频队列和播放线程
audio_queue = queue.Queue()
audio_player = None
# 添加全局中断标志
interrupt_flag = threading.Event()

# 初始化PyAudio
p = pyaudio.PyAudio()
RATE = 24000  # 采样率 24kHz
CHUNK = 3200  # 每个音频块的大小
FORMAT = pyaudio.paInt16  # 16位PCM格式
CHANNELS = 1  # 单声道

# 将OmniRealtimeClient类直接放在这里（已包含硬编码API密钥）
class TurnDetectionMode(Enum):
    SERVER_VAD = "server_vad"
    MANUAL = "manual"

class OmniRealtimeClient:
    """
    与 Omni Realtime API 交互的演示客户端。
    """
    def __init__(
        self,
        base_url,
        model: str = "",
        voice: str = "Ethan",
        turn_detection_mode: TurnDetectionMode = TurnDetectionMode.MANUAL,
        on_text_delta: Optional[Callable[[str], None]] = None,
        on_audio_delta: Optional[Callable[[bytes], None]] = None,
        on_interrupt: Optional[Callable[[], None]] = None,
        on_input_transcript: Optional[Callable[[str], None]] = None,
        on_output_transcript: Optional[Callable[[str], None]] = None,
        extra_event_handlers: Optional[Dict[str, Callable[[Dict[str, Any]], None]]] = None
    ):
        self.base_url = base_url
        self.api_key = DASHSCOPE_API_KEY  # 使用硬编码的API密钥
        self.model = model
        self.voice = voice
        self.ws = None
        self.on_text_delta = on_text_delta
        self.on_audio_delta = on_audio_delta
        self.on_interrupt = on_interrupt
        self.on_input_transcript = on_input_transcript
        self.on_output_transcript = on_output_transcript
        self.turn_detection_mode = turn_detection_mode
        self.extra_event_handlers = extra_event_handlers or {}

        # 当前回复状态
        self._current_response_id = None
        self._current_item_id = None
        self._is_responding = False
        # 输入/输出转录打印状态
        self._print_input_transcript = False
        self._output_transcript_buffer = ""

    async def connect(self) -> None:
        """与 Realtime API 建立 WebSocket 连接。"""
        url = f"{self.base_url}?model={self.model}"
        headers = {
            "Authorization": f"Bearer {self.api_key}"
        }

        self.ws = await websockets.connect(url, additional_headers=headers)

        # 设置默认会话配置
        if self.turn_detection_mode == TurnDetectionMode.MANUAL:
            await self.update_session({
                "modalities": ["text", "audio"],
                "voice": self.voice,
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                "input_audio_transcription": {
                    "model": "gummy-realtime-v1"
                },
                "turn_detection" : None
            })
        elif self.turn_detection_mode == TurnDetectionMode.SERVER_VAD:
            await self.update_session({
                "modalities": ["text", "audio"],
                "voice": self.voice,
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                "input_audio_transcription": {
                    "model": "gummy-realtime-v1"
                },
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": 0.1,
                    "prefix_padding_ms": 500,
                    "silence_duration_ms": 900
                }
            })
        else:
            raise ValueError(f"Invalid turn detection mode: {self.turn_detection_mode}")

    async def send_event(self, event) -> None:
        event['event_id'] = "event_" + str(int(time.time() * 1000))
        print(f" Send event: type={event['type']}, event_id={event['event_id']}")
        await self.ws.send(json.dumps(event))

    async def update_session(self, config: Dict[str, Any]) -> None:
        """更新会话配置。"""
        event = {
            "type": "session.update",
            "session": config
        }
        print("update session: ", event)
        await self.send_event(event)

    async def stream_audio(self, audio_chunk: bytes) -> None:
        """向 API 流式发送原始音频数据。"""
        # 仅支持 16bit 16kHz 单声道 PCM
        audio_b64 = base64.b64encode(audio_chunk).decode()

        append_event = {
            "type": "input_audio_buffer.append",
            "audio": audio_b64
        }
        await self.send_event(append_event)

    async def commit_audio_buffer(self) -> None:
        """提交音频缓冲区以触发处理。"""
        event = {
            "type": "input_audio_buffer.commit"
        }
        await self.send_event(event)

    async def append_image(self, image_chunk: bytes) -> None:
        """向视频缓冲区追加图像数据。"""
        image_b64 = base64.b64encode(image_chunk).decode()

        event = {
            "type": "input_image_buffer.append",
            "image": image_b64
        }
        await self.send_event(event)

    async def create_response(self) -> None:
        """向 API 请求生成回复（仅在手动模式下需要调用）。"""
        event = {
            "type": "response.create",
            "response": {
                "instructions": "You are a helpful assistant.",
                "modalities": ["text", "audio"]
            }
        }
        print("create response: ", event)
        await self.send_event(event)

    async def cancel_response(self) -> None:
        """取消当前回复。"""
        event = {
            "type": "response.cancel"
        }
        await self.send_event(event)

    async def handle_interruption(self):
        """处理用户对当前回复的打断。"""
        if not self._is_responding:
            return

        print(" Handling interruption")

        # 1. 取消当前回复
        if self._current_response_id:
            await self.cancel_response()

        self._is_responding = False
        self._current_response_id = None
        self._current_item_id = None

    async def handle_messages(self) -> None:
        try:
            async for message in self.ws:
                event = json.loads(message)
                event_type = event.get("type")
                
                if event_type != "response.audio.delta":
                    print(" event: ", event)
                else:
                    print(" event_type: ", event_type)

                if event_type == "error":
                    print(" Error: ", event['error'])
                    continue
                elif event_type == "response.created":
                    self._current_response_id = event.get("response", {}).get("id")
                    self._is_responding = True
                elif event_type == "response.output_item.added":
                    self._current_item_id = event.get("item", {}).get("id")
                elif event_type == "response.done":
                    self._is_responding = False
                    self._current_response_id = None
                    self._current_item_id = None
                # Handle interruptions
                elif event_type == "input_audio_buffer.speech_started":
                    print(" Speech detected")
                    if self._is_responding:
                        print(" Handling interruption")
                        await self.handle_interruption()

                    if self.on_interrupt:
                        print(" Handling on_interrupt, stop playback")
                        self.on_interrupt()
                elif event_type == "input_audio_buffer.speech_stopped":
                    print(" Speech ended")
                # Handle normal response events
                elif event_type == "response.text.delta":
                    if self.on_text_delta:
                        self.on_text_delta(event["delta"])
                elif event_type == "response.audio.delta":
                    if self.on_audio_delta:
                        audio_bytes = base64.b64decode(event["delta"])
                        self.on_audio_delta(audio_bytes)
                elif event_type == "conversation.item.input_audio_transcription.completed":
                    transcript = event.get("transcript", "")
                    if self.on_input_transcript:
                        await asyncio.to_thread(self.on_input_transcript,transcript)
                        self._print_input_transcript = True
                elif event_type == "response.audio_transcript.delta":
                    if self.on_output_transcript:
                        delta = event.get("delta", "")
                        if not self._print_input_transcript:
                            self._output_transcript_buffer += delta
                        else:
                            if self._output_transcript_buffer:
                                await asyncio.to_thread(self.on_output_transcript,self._output_transcript_buffer)
                                self._output_transcript_buffer = ""
                            await asyncio.to_thread(self.on_output_transcript,delta)
                elif event_type == "response.audio_transcript.done":
                    self._print_input_transcript = False
                elif event_type in self.extra_event_handlers:
                    self.extra_event_handlers[event_type](event)

        except websockets.exceptions.ConnectionClosed:
            print(" Connection closed")
        except Exception as e:
            print(" Error in message handling: ", str(e))

    async def close(self) -> None:
        """关闭 WebSocket 连接。"""
        if self.ws:
            await self.ws.close()

def clear_audio_queue():
    """清空音频队列"""
    with audio_queue.mutex:
        audio_queue.queue.clear()
    print("音频队列已清空")

def handle_interrupt():
    """处理中断事件 - 立即停止音频播放"""
    print("检测到语音输入，停止音频播放")
    interrupt_flag.set()  # 设置中断标志
    clear_audio_queue()  # 清空队列

def audio_player_thread():
    """后台线程用于播放音频数据"""
    stream = p.open(
        format=FORMAT,
        channels=CHANNELS,
        rate=24000,
        output=True,
        frames_per_buffer=CHUNK,
    )

    try:
        while True:
            try:
                # 检查中断标志
                if interrupt_flag.is_set():
                    print("音频播放被中断")
                    interrupt_flag.clear()  # 清除中断标志
                    continue
                
                # 从队列获取音频数据
                audio_data = audio_queue.get(block=True, timeout=0.5)
                if audio_data is None:  # 结束信号
                    break
                
                # 再次检查中断标志（在播放前）
                if interrupt_flag.is_set():
                    print("音频播放被中断")
                    interrupt_flag.clear()  # 清除中断标志
                    audio_queue.task_done()
                    continue
                
                # 播放音频数据
                stream.write(audio_data)
                audio_queue.task_done()
            except queue.Empty:
                # 如果队列为空，继续等待
                continue
    finally:
        # 清理
        stream.stop_stream()
        stream.close()


def start_audio_player():
    """启动音频播放线程"""
    global audio_player
    if audio_player is None or not audio_player.is_alive():
        audio_player = threading.Thread(target=audio_player_thread, daemon=True)
        audio_player.start()


def handle_audio_data(audio_data):
    """处理接收到的音频数据"""
    # 打印接收到的音频数据长度（调试用）
    print(f"\n接收到音频数据: {len(audio_data)} 字节")
    # 将音频数据放入队列
    audio_queue.put(audio_data)


async def start_microphone_streaming(client: OmniRealtimeClient):
    CHUNK = 3200
    FORMAT = pyaudio.paInt16
    CHANNELS = 1
    RATE = 24000

    p = pyaudio.PyAudio()
    stream = p.open(
        format=FORMAT, channels=CHANNELS, rate=RATE, input=True, frames_per_buffer=CHUNK
    )

    try:
        print("开始录音，请讲话...")
        while True:
            audio_data = stream.read(CHUNK)
            encoded_data = base64.b64encode(audio_data).decode("utf-8")

            eventd = {
                "event_id": "event_" + str(int(time.time() * 1000)),
                "type": "input_audio_buffer.append",
                "audio": encoded_data,
            }
            await client.send_event(eventd)

            # 保持较短的等待时间以模拟实时交互
            await asyncio.sleep(0.05)
    finally:
        stream.stop_stream()
        stream.close()
        p.terminate()


async def main():
    # 启动音频播放线程
    start_audio_player()

    realtime_client = OmniRealtimeClient(
        base_url="wss://dashscope.aliyuncs.com/api-ws/v1/realtime",
        model="qwen-omni-turbo-realtime",
        voice="Chelsie",
        on_text_delta=lambda text: print(f"\nAssistant: {text}", end="", flush=True),
        on_audio_delta=handle_audio_data,
        on_interrupt=handle_interrupt,  # 添加中断回调函数
        turn_detection_mode=TurnDetectionMode.SERVER_VAD,
    )

    try:
        await realtime_client.connect()
        # 启动消息处理和麦克风录音
        message_handler = asyncio.create_task(realtime_client.handle_messages())
        streaming_task = asyncio.create_task(
            start_microphone_streaming(realtime_client)
        )

        while True:
            await asyncio.Queue().get()
    except Exception as e:
        print(f"Error: {e}")
    finally:
        # 结束音频播放线程
        audio_queue.put(None)
        if audio_player:
            audio_player.join(timeout=1)
        await realtime_client.close()
        p.terminate()

if __name__ == "__main__":
    asyncio.run(main())