 # -- coding: utf-8 --

import asyncio
import websockets
import json
import base64
import time
from typing import Optional, Callable, List, Dict, Any
from enum import Enum

# 在这里硬编码API密钥
API_KEY = "sk-4790da8d470049e9be356a6c0179e5b9"  

class TurnDetectionMode(Enum):
    SERVER_VAD = "server_vad"
    MANUAL = "manual"

class OmniRealtimeClient:
    """
    与 Omni Realtime API 交互的演示客户端。
    
    属性说明:
        base_url (str): Realtime API 的基础地址。
        model (str): 用于聊天的 Omni 模型名称。
        voice (str): 服务器合成语音所使用的声音。
        turn_detection_mode (TurnDetectionMode): 轮次检测模式。
        on_text_delta (Callable[[str], None]): 文本增量回调函数。
        on_audio_delta (Callable[[bytes], None]): 音频增量回调函数。
        on_input_transcript (Callable[[str], None]): 输入转录文本回调函数。
        on_interrupt (Callable[[], None]): 用户打断回调函数。
        on_output_transcript (Callable[[str], None]): 输出转录文本回调函数。
        extra_event_handlers (Dict[str, Callable[[Dict[str, Any]], None]]): 其他事件处理器。
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
        self.api_key = API_KEY  # 使用硬编码的API密钥
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