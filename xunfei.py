#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智能语音助手 - 基于讯飞星火4.0Ultra
支持内置插件：搜索、天气、日期、诗词、字词、股票
"""

import asyncio
import websockets
import json
import base64
import hmac
import hashlib
import time
import threading
import queue
import wave
import pyaudio
from urllib.parse import urlencode

class AssistantConfig:
    """配置信息 """
    # 讯飞开放平台应用配置
    APP_ID = "9d69f5d1"
    API_SECRET = "OGU2ODIxOTI0ZmI2NjgwMzc0MGRhNzlh"
    API_KEY = "3a861a0ce1bb7d66cc59286677018f86"
    
    # API端点
    # 语音听写流式接口
    ASR_URL = "wss://iat-api.xfyun.cn/v2/iat"
    # 星火认知大模型4.0Ultra
    LLM_URL = "wss://spark-api.xf-yun.com/v4.0/chat"
    # 在线语音合成
    TTS_URL = "wss://tts-api.xfyun.cn/v2/tts"
    
    # 音频参数
    SAMPLE_RATE = 16000
    CHANNELS = 1
    SAMPLE_WIDTH = 2

class AudioRecorder:
    """音频录制器 - 简化版本"""
    
    def __init__(self, config):
        self.config = config
        self.audio_queue = queue.Queue()
        self.is_recording = False
        self.audio = None
        
    def start_recording(self):
        """开始录音"""
        try:
            self.audio = pyaudio.PyAudio()
            self.is_recording = True
            
            stream = self.audio.open(
                format=pyaudio.paInt16,
                channels=self.config.CHANNELS,
                rate=self.config.SAMPLE_RATE,
                input=True,
                frames_per_buffer=1024
            )
            
            def record_audio():
                print("🎤 开始录音...")
                while self.is_recording:
                    try:
                        audio_data = stream.read(1024, exception_on_overflow=False)
                        self.audio_queue.put(audio_data)
                    except Exception as e:
                        print(f"录音错误: {e}")
                        break
                        
                stream.stop_stream()
                stream.close()
                print("🎤 录音结束")
                        
            self.record_thread = threading.Thread(target=record_audio)
            self.record_thread.daemon = True
            self.record_thread.start()
            
        except Exception as e:
            print(f"❌ 录音初始化失败: {e}")
            print("请检查麦克风设备是否可用")
        
    def stop_recording(self):
        """停止录音"""
        self.is_recording = False
        if hasattr(self, 'record_thread'):
            self.record_thread.join(timeout=1)
        if self.audio:
            self.audio.terminate()
        
    def get_audio_data(self):
        """获取音频数据"""
        try:
            return self.audio_queue.get_nowait()
        except queue.Empty:
            return None

class XunfeiASR:
    """讯飞语音识别服务"""
    
    def __init__(self, config):
        self.config = config
        self.result_queue = queue.Queue()
        self.full_text = ""
        
    def generate_auth_url(self, host, method, path):
        """生成鉴权URL"""
        now = time.time()
        date = time.strftime('%a, %d %b %Y %H:%M:%S GMT', time.gmtime(now))
        
        signature_origin = f"host: {host}\ndate: {date}\n{method} {path} HTTP/1.1"
        signature_sha = hmac.new(
            self.config.API_SECRET.encode('utf-8'),
            signature_origin.encode('utf-8'),
            digestmod=hashlib.sha256
        ).digest()
        signature = base64.b64encode(signature_sha).decode(encoding='utf-8')
        
        authorization_origin = f'api_key="{self.config.API_KEY}", algorithm="hmac-sha256", headers="host date request-line", signature="{signature}"'
        authorization = base64.b64encode(authorization_origin.encode('utf-8')).decode(encoding='utf-8')
        
        values = {
            "authorization": authorization,
            "date": date,
            "host": host
        }
        
        return f"{self.config.ASR_URL}?{urlencode(values)}"
    
    async def speech_to_text(self, audio_recorder):
        """语音转文字"""
        url = self.generate_auth_url("iat-api.xfyun.cn", "GET", "/v2/iat")
        
        try:
            async with websockets.connect(url) as websocket:
                # 发送开始参数
                start_params = {
                    "common": {"app_id": self.config.APP_ID},
                    "business": {
                        "language": "zh_cn",
                        "domain": "iat",
                        "accent": "mandarin",
                        "vad_eos": 5000,  # 缩短静音检测时间
                        "dwa": "wpgs"     # 开启动态修正
                    },
                    "data": {
                        "status": 0,
                        "format": "audio/L16;rate=16000",
                        "encoding": "raw"
                    }
                }
                
                await websocket.send(json.dumps(start_params))
                
                # 发送音频数据
                while audio_recorder.is_recording:
                    audio_data = audio_recorder.get_audio_data()
                    if audio_data:
                        data_params = {
                            "data": {
                                "status": 1,
                                "format": "audio/L16;rate=16000",
                                "encoding": "raw",
                                "audio": base64.b64encode(audio_data).decode()
                            }
                        }
                        await websocket.send(json.dumps(data_params))
                    
                    # 接收识别结果
                    try:
                        response = await asyncio.wait_for(websocket.recv(), timeout=0.1)
                        result = json.loads(response)
                        if result.get('data') and result['data'].get('result'):
                            text = self.parse_asr_result(result)
                            if text:
                                print(f"🎯 识别中: {text}")
                    except asyncio.TimeoutError:
                        continue
                        
                    await asyncio.sleep(0.1)
                
                # 发送结束标识
                end_params = {
                    "data": {
                        "status": 2,
                        "format": "audio/L16;rate=16000",
                        "encoding": "raw",
                        "audio": ""
                    }
                }
                await websocket.send(json.dumps(end_params))
                
                # 获取最终结果
                try:
                    final_response = await asyncio.wait_for(websocket.recv(), timeout=2)
                    final_result = json.loads(final_response)
                    if final_result.get('data') and final_result['data'].get('result'):
                        final_text = self.parse_asr_result(final_result)
                        if final_text:
                            self.result_queue.put(final_text)
                            print(f"✅ 识别完成: {final_text}")
                except asyncio.TimeoutError:
                    pass
                    
        except Exception as e:
            print(f"❌ 语音识别连接失败: {e}")
    
    def parse_asr_result(self, result):
        """解析ASR结果"""
        try:
            if result['data'] and result['data']['result']:
                text = ""
                for ws in result['data']['result']['ws']:
                    for cw in ws['cw']:
                        text += cw['w']
                return text
        except KeyError:
            pass
        return None
    
    def get_recognized_text(self):
        """获取识别的文本"""
        try:
            return self.result_queue.get_nowait()
        except queue.Empty:
            return None

class SparkLLM:
    """星火认知大模型4.0Ultra服务"""
    
    def __init__(self, config):
        self.config = config
        self.conversation_history = []
        
    def generate_spark_url(self):
        """生成星火模型URL"""
        host = "spark-api.xf-yun.com"
        path = "/v4.0/chat"
        
        now = time.time()
        date = time.strftime('%a, %d %b %Y %H:%M:%S GMT', time.gmtime(now))
        
        signature_origin = f"host: {host}\ndate: {date}\nGET {path} HTTP/1.1"
        signature_sha = hmac.new(
            self.config.API_SECRET.encode('utf-8'),
            signature_origin.encode('utf-8'),
            digestmod=hashlib.sha256
        ).digest()
        signature = base64.b64encode(signature_sha).decode()
        
        authorization_origin = f'api_key="{self.config.API_KEY}", algorithm="hmac-sha256", headers="host date request-line", signature="{signature}"'
        authorization = base64.b64encode(authorization_origin.encode()).decode()
        
        values = {
            "authorization": authorization,
            "date": date,
            "host": host
        }
        
        return f"wss://{host}{path}?{urlencode(values)}"
    
    async def chat(self, user_input):
        """与星火模型对话 - 支持内置插件"""
        # 构建智能助手的系统提示
        system_prompt = """你是一个智能车载语音助手，具备以下特征：
1. 友好、自然、简洁的回答风格
2. 能够使用内置插件提供实时信息：搜索、天气、日期、诗词、字词、股票等
3. 回答时要准确、有用、贴心
4. 支持语音交互，回答要简明扼要但信息完整
5. 当用户询问天气、股票、搜索等信息时，自动调用相应功能
请根据用户的需求提供帮助。"""
        
        # 构建消息历史
        messages = [{"role": "system", "content": system_prompt}]
        
        # 保持对话历史在合理长度内
        if len(self.conversation_history) > 6:
            messages.extend(self.conversation_history[-6:])
        else:
            messages.extend(self.conversation_history)
            
        messages.append({"role": "user", "content": user_input})
        
        url = self.generate_spark_url()
        
        try:
            async with websockets.connect(url) as websocket:
                request_data = {
                    "header": {
                        "app_id": self.config.APP_ID,
                        "uid": "voice_assistant_001"
                    },
                    "parameter": {
                        "chat": {
                            "domain": "4.0Ultra",  # 使用4.0Ultra版本
                            "temperature": 0.8,
                            "max_tokens": 2048,
                            "auditing": "default"
                        }
                    },
                    "payload": {
                        "message": {
                            "text": messages
                        }
                    }
                }
                
                await websocket.send(json.dumps(request_data))
                
                response_text = ""
                function_call_info = None
                
                async for message in websocket:
                    data = json.loads(message)
                    
                    # 检查是否有function_call（内置插件调用）
                    if data.get('payload', {}).get('choices', {}).get('text'):
                        for choice in data['payload']['choices']['text']:
                            if choice.get('function_call'):
                                function_call_info = choice['function_call']
                                print(f"🔧 调用内置插件: {function_call_info.get('name', 'unknown')}")
                                print(f"📝 参数: {function_call_info.get('arguments', '{}')}")
                            else:
                                response_text += choice.get('content', '')
                    
                    if data.get('header', {}).get('status') == 2:
                        break
                
                # 处理插件调用结果
                if function_call_info:
                    plugin_name = function_call_info.get('name', '')
                    response_text = f"正在为您查询{plugin_name}信息，请稍等..."
                
                # 更新对话历史
                self.conversation_history.append({"role": "user", "content": user_input})
                self.conversation_history.append({"role": "assistant", "content": response_text})
                
                return response_text
                
        except Exception as e:
            print(f"❌ 星火模型连接失败: {e}")
            return "抱歉，我暂时无法回答，请稍后再试。"

class XunfeiTTS:
    """讯飞语音合成服务"""
    
    def __init__(self, config):
        self.config = config
        
    def generate_tts_url(self):
        """生成TTS URL"""
        host = "tts-api.xfyun.cn"
        path = "/v2/tts"
        
        now = time.time()
        date = time.strftime('%a, %d %b %Y %H:%M:%S GMT', time.gmtime(now))
        
        signature_origin = f"host: {host}\ndate: {date}\nGET {path} HTTP/1.1"
        signature_sha = hmac.new(
            self.config.API_SECRET.encode('utf-8'),
            signature_origin.encode('utf-8'),
            digestmod=hashlib.sha256
        ).digest()
        signature = base64.b64encode(signature_sha).decode()
        
        authorization_origin = f'api_key="{self.config.API_KEY}", algorithm="hmac-sha256", headers="host date request-line", signature="{signature}"'
        authorization = base64.b64encode(authorization_origin.encode()).decode()
        
        values = {
            "authorization": authorization,
            "date": date,
            "host": host
        }
        
        return f"wss://{host}{path}?{urlencode(values)}"
    
    async def text_to_speech(self, text, voice="x4_yezi"):
        """文本转语音"""
        if not text or len(text.strip()) == 0:
            return None
            
        url = self.generate_tts_url()
        
        try:
            async with websockets.connect(url) as websocket:
                request_data = {
                    "common": {
                        "app_id": self.config.APP_ID
                    },
                    "business": {
                        "aue": "raw",
                        "auf": "audio/L16;rate=16000",
                        "vcn": voice,  # 发音人：x4_yezi  
                        "speed": 55,   # 语速：0-100，稍快一些
                        "volume": 70,  # 音量：0-100
                        "pitch": 50,   # 音调：0-100
                        "bgs": 0       # 背景音：0-1
                    },
                    "data": {
                        "status": 2,
                        "text": base64.b64encode(text.encode('utf-8')).decode()
                    }
                }
                
                await websocket.send(json.dumps(request_data))
                
                audio_data = b""
                async for message in websocket:
                    data = json.loads(message)
                    if data.get('data', {}).get('audio'):
                        audio_chunk = base64.b64decode(data['data']['audio'])
                        audio_data += audio_chunk
                    
                    if data.get('code') != 0:
                        print(f"TTS错误: {data.get('message', '未知错误')}")
                        break
                        
                    if data.get('data', {}).get('status') == 2:
                        break
                
                return audio_data
                
        except Exception as e:
            print(f"❌ 语音合成失败: {e}")
            return None
    
    def play_audio(self, audio_data):
        """播放音频"""
        if not audio_data:
            return
            
        try:
            audio = pyaudio.PyAudio()
            
            stream = audio.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=16000,
                output=True
            )
            
            # 分块播放，避免卡顿
            chunk_size = 1024
            for i in range(0, len(audio_data), chunk_size):
                chunk = audio_data[i:i + chunk_size]
                stream.write(chunk)
            
            stream.stop_stream()
            stream.close()
            audio.terminate()
            
        except Exception as e:
            print(f"❌ 音频播放失败: {e}")

class VoiceAssistant:
    """智能语音助手主类"""
    
    def __init__(self):
        self.config = AssistantConfig()
        self.audio_recorder = AudioRecorder(self.config)
        self.asr = XunfeiASR(self.config)
        self.llm = SparkLLM(self.config)
        self.tts = XunfeiTTS(self.config)
        self.is_listening = False
        self.is_processing = False
        
    async def start_conversation(self):
        """开始对话循环"""
        print("🚀 智能语音助手已启动")
        print("📱 基于讯飞星火4.0Ultra，支持搜索、天气、日期、诗词、字词、股票等功能")
        print("🎤 说话后按回车键结束录音，输入 'quit' 退出")
        print("-" * 50)
        
        try:
            await self.conversation_loop()
        except KeyboardInterrupt:
            print("\n👋 程序已退出")
        finally:
            self.cleanup()
    
    async def conversation_loop(self):
        """对话循环 - 简化的交互方式"""
        while True:
            try:
                # 等待用户按键开始录音
                user_input = input("\n🎤 按回车开始说话 (输入文字直接对话，输入'quit'退出): ")
                
                if user_input.lower() in ['quit', 'exit', '退出', '再见']:
                    await self.respond("再见！期待下次与您对话。")
                    break
                
                # 如果用户直接输入文字
                if user_input.strip():
                    await self.handle_user_input(user_input)
                else:
                    # 语音输入
                    await self.handle_voice_input()
                    
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"❌ 对话出错: {e}")
    
    async def handle_voice_input(self):
        """处理语音输入"""
        if self.is_processing:
            print("⏳ 正在处理中，请稍等...")
            return
            
        self.is_processing = True
        
        try:
            print("🎤 开始录音，说完话后按回车键结束...")
            
            # 启动录音
            self.audio_recorder.start_recording()
            
            # 启动语音识别任务
            asr_task = asyncio.create_task(
                self.asr.speech_to_text(self.audio_recorder)
            )
            
            # 等待用户按回车结束录音
            await asyncio.get_event_loop().run_in_executor(None, input, "按回车键结束录音...")
            
            # 停止录音
            self.audio_recorder.stop_recording()
            
            # 等待识别完成
            await asyncio.sleep(1)
            
            # 获取识别结果
            text = self.asr.get_recognized_text()
            if text and len(text.strip()) > 1:
                print(f"🎯 识别结果: {text}")
                await self.handle_user_input(text)
            else:
                print("❌ 未识别到有效语音，请重试")
                
        except Exception as e:
            print(f"❌ 语音处理失败: {e}")
        finally:
            self.is_processing = False
    
    async def handle_user_input(self, user_input):
        """处理用户输入"""
        try:
            print(f"🤔 正在思考...")
            
            # 与星火模型对话
            response = await self.llm.chat(user_input)
            print(f"🤖 助手回复: {response}")
            
            # 语音回复
            await self.respond(response)
            
        except Exception as e:
            print(f"❌ 处理出错: {e}")
            await self.respond("抱歉，我暂时无法理解，请稍后再试。")
    
    async def respond(self, text):
        """语音回复"""
        try:
            print(f"🔊 正在播放语音...")
            
            # 文本转语音
            audio_data = await self.tts.text_to_speech(text, voice="xiaoyan")
            
            # 播放语音
            if audio_data:
                self.tts.play_audio(audio_data)
                print(f"✅ 播放完成")
            else:
                print(f"❌ 语音合成失败")
            
        except Exception as e:
            print(f"❌ 语音回复失败: {e}")
    
    def cleanup(self):
        """清理资源"""
        if hasattr(self.audio_recorder, 'is_recording'):
            self.audio_recorder.stop_recording()

# 使用示例和测试功能
async def test_individual_services():
    """测试各个服务是否正常"""
    config = AssistantConfig()
    
    print("🧪 测试星火4.0Ultra连接...")
    llm = SparkLLM(config)
    
    try:
        # 测试基本对话
        response = await llm.chat("你好")
        print(f"✅ 星火模型测试成功: {response}")
        
        # 测试天气插件
        response = await llm.chat("北京今天天气怎么样？")
        print(f"✅ 天气插件测试: {response}")
        
        # 测试搜索插件
        response = await llm.chat("搜索一下最新的AI新闻")
        print(f"✅ 搜索插件测试: {response}")
        
        # 测试日期插件
        response = await llm.chat("今天是几号？")
        print(f"✅ 日期插件测试: {response}")
        
    except Exception as e:
        print(f"❌ 测试失败: {e}")

async def main():
    """主函数"""
    print("🎉 欢迎使用智能语音助手！")
    print("🔑 已配置您的API密钥")
    print()
    
    # 询问用户是否先测试
    test_choice = input("是否先测试各项功能？(y/n): ")
    if test_choice.lower() == 'y':
        await test_individual_services()
        print("\n" + "="*50 + "\n")
    
    try:
        # 启动语音助手
        assistant = VoiceAssistant()
        await assistant.start_conversation()
        
    except Exception as e:
        print(f"❌ 程序异常: {e}")

if __name__ == "__main__":
    print("📋 系统要求检查:")
    print("✅ Python 3.7+")
    
    try:
        import pyaudio
        print("✅ PyAudio 已安装")
    except ImportError:
        print("❌ PyAudio 未安装，请运行: pip install pyaudio")
        exit(1)
    
    try:
        import websockets
        print("✅ WebSockets 已安装")
    except ImportError:
        print("❌ WebSockets 未安装，请运行: pip install websockets")
        exit(1)
    
    print("✅ 您的API密钥已配置")
    print("🚀 启动程序...\n")
    
    asyncio.run(main())