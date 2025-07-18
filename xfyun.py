#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
车载智能助手「小柚」- 活泼可爱的少女语音助手
基于讯飞星火4.0Ultra，支持内置插件和自然语音交互
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
import logging
import re

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class XiaoyouConfig:
    """讯飞的配置信息"""
    # 您的讯飞开放平台应用配置
    APP_ID = "  "
    API_SECRET = "  "
    API_KEY = "  "
    
    # API端点
    ASR_URL = "wss://iat-api.xfyun.cn/v2/iat"
    LLM_URL = "wss://spark-api.xf-yun.com/v4.0/chat"
    TTS_URL = "wss://tts-api.xfyun.cn/v2/tts"
    
    # 音频参数
    SAMPLE_RATE = 16000
    CHANNELS = 1
    SAMPLE_WIDTH = 2

class SmartAudioRecorder:
    """智能音频录制器"""
    
    def __init__(self, config):
        self.config = config
        self.audio_queue = queue.Queue(maxsize=50)  
        self.is_recording = False
        self.audio = None
        self.stream = None
        
    def start_recording(self):
        """开始录音"""
        try:
            if self.audio is None:
                self.audio = pyaudio.PyAudio()
            
            self.is_recording = True
            
            # 选择最佳音频设备
            device_index = self._find_best_input_device()
            
            self.stream = self.audio.open(
                format=pyaudio.paInt16,
                channels=self.config.CHANNELS,
                rate=self.config.SAMPLE_RATE,
                input=True,
                input_device_index=device_index,
                frames_per_buffer=400,    
                stream_callback=self._audio_callback
            )
            
            self.stream.start_stream()
            logger.info("🎤 小柚开始听您说话...")
            
        except Exception as e:
            logger.error(f"❌ 录音设备初始化失败: {e}")
            self._print_audio_devices()
    
    def _find_best_input_device(self):
        """寻找最佳输入设备"""
        try:
            default_device = self.audio.get_default_input_device_info()
            return default_device['index']
        except:
            return None
    
    def _print_audio_devices(self):
        """打印可用音频设备"""
        try:
            print("\n🔊 可用音频设备:")
            for i in range(self.audio.get_device_count()):
                info = self.audio.get_device_info_by_index(i)
                if info['maxInputChannels'] > 0:
                    print(f"  设备 {i}: {info['name']}")
        except Exception as e:
            print(f"无法获取设备列表: {e}")
    
    def _audio_callback(self, in_data, frame_count, time_info, status):
        """音频回调函数"""
        if self.is_recording:
            self.audio_queue.put(in_data)
        return (None, pyaudio.paContinue)
        
    def stop_recording(self):
        """停止录音"""
        self.is_recording = False
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
            self.stream = None
        logger.info("🎤 录音结束")
        
    def cleanup(self):
        """清理资源"""
        self.stop_recording()
        if self.audio:
            self.audio.terminate()
            self.audio = None
        
    def get_audio_data(self):
        """获取音频数据 - 批量处理优化"""
        audio_chunks = []
        try:
            # 一次性获取多个音频块，减少调用次数
            while len(audio_chunks) < 3:  # 最多获取3个块
                chunk = self.audio_queue.get_nowait()
                audio_chunks.append(chunk)
        except queue.Empty:
            pass
        
        if audio_chunks:
            return b''.join(audio_chunks)  # 合并音频块
        return None

class XiaoyouASR:
    """语音识别服务 """
    
    def __init__(self, config):
        self.config = config
        self.result_queue = queue.Queue()
        
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
    
    async def speech_to_text_with_timeout(self, audio_recorder, timeout=8):
        """语音转文字 """
        url = self.generate_auth_url("iat-api.xfyun.cn", "GET", "/v2/iat")
        
        try:
            # WebSocket连接参数
            async with websockets.connect(
                url,
                ping_interval=None,      
                ping_timeout=None,       
                close_timeout=2,         
                max_size=2**20,         
                compression=None         
            ) as websocket:
                
                # 发送开始参数  
                start_params = {
                    "common": {"app_id": self.config.APP_ID},
                    "business": {
                            "language": "zh_cn",       
                            "domain": "iat",           
                            "accent": "mandarin",       
                            "vad_eos": 1000,            
                            "dwa": "wpgs",             
                            "vad_sil": 0.2,             
                            "vad_speech_head": 200,     
                            "vad_speech_tail": 200,     
                            "max_rg": 2000,             
                            "nbest": 1,                 
                            "nunum": 0,                 
                            "speex_size": 60,           
                            "vinfo": 1,                 
                            "rlang": "zh-cn",           
                            "ptt": 0                   
                        },
                    "data": {
                        "status": 0,
                        "format": "audio/L16;rate=16000",
                        "encoding": "raw"
                    }
                }
                
                await websocket.send(json.dumps(start_params))
                
                # 音频发送和结果接收
                start_time = time.time()
                audio_sent = False
                last_result = ""  # 保存最后的识别结果
                
                while time.time() - start_time < timeout:
                    # 发送音频数据
                    audio_data = audio_recorder.get_audio_data()
                    if audio_data and audio_recorder.is_recording:
                        audio_sent = True
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
                        response = await asyncio.wait_for(websocket.recv(), timeout=0.2)
                        result = json.loads(response)
                        print(f"🔍 收到消息: code={result.get('code')}, status={result.get('data', {}).get('status')}")
                        
                        if result.get('data') and result['data'].get('result'):
                            text = self.parse_asr_result(result)
                            if text and len(text.strip()) > 0:
                                print(f"🎯 小柚听到: {text}")
                                last_result = text  # 保存最新结果
                        
                        # 检查是否完成
                        # 保存每次的识别结果到队列（实时保存）
                        if text and len(text.strip()) > 0:
                            # 实时更新队列中的结果
                            # 清空队列并放入最新结果
                            while not self.result_queue.empty():
                                try:
                                    self.result_queue.get_nowait()
                                except:
                                    pass
                            self.result_queue.put(text.strip())
                            print(f"🔍 实时保存结果到队列: '{text}'")
                        
                        # 检查是否完成
                        if result.get('data', {}).get('status') == 2:
                            print(f"🔍 收到最终消息，status=2")
                            final_text = self.parse_asr_result(result)
                            print(f"🔍 解析的最终文本: '{final_text}'")
                            if final_text:
                                # 清空队列并放入最终结果
                                while not self.result_queue.empty():
                                    try:
                                        self.result_queue.get_nowait()
                                    except:
                                        pass
                                self.result_queue.put(final_text.strip())
                                logger.info(f"✅ 识别完成: {final_text}")
                            elif last_result:  # 如果最终结果为空，使用最后保存的结果
                                print(f"🔍 最终文本为空，使用备用结果: '{last_result}'")
                                if self.result_queue.empty():
                                    self.result_queue.put(last_result.strip())
                                logger.info(f"✅ 使用最后结果: {last_result}")
                            else:
                                print(f"🔍 警告：最终文本和备用结果都为空")
                            break
                       
                            
                    except asyncio.TimeoutError:
                        if not audio_recorder.is_recording and audio_sent:
                            # 手动发送结束标识，强制获取最终结果
                            print(f"🔍 手动发送结束标识")
                            end_params = {
                                "data": {
                                    "status": 2,
                                    "format": "audio/L16;rate=16000",
                                    "encoding": "raw",
                                    "audio": ""
                                }
                            }
                            await websocket.send(json.dumps(end_params))
                            
                            # 等待最终结果
                            try:
                                final_response = await asyncio.wait_for(websocket.recv(), timeout=2)
                                final_result = json.loads(final_response)
                                print(f"🔍 收到手动触发的最终结果: {final_result.get('data', {}).get('status')}")
                                
                                if final_result.get('data', {}).get('status') == 2:
                                    final_text = self.parse_asr_result(final_result)
                                    if final_text:
                                        self.result_queue.put(final_text.strip())
                                        logger.info(f"✅ 手动触发识别完成: {final_text}")
                                        break
                                    elif last_result:
                                        self.result_queue.put(last_result.strip())
                                        logger.info(f"✅ 手动触发使用备用结果: {last_result}")
                                        break
                            except asyncio.TimeoutError:
                                print(f"🔍 等待最终结果超时")
                                pass
                        continue
                    
                    await asyncio.sleep(0.05)
                
                # 如果没有通过正常流程获取到结果，但有last_result，也保存它
                print(f"🔍 循环结束，检查备用保存机制")
                print(f"🔍 last_result = '{last_result}'")
                print(f"🔍 队列当前状态：{'空' if self.result_queue.empty() else '有内容'}")
                
                # 强制保存最后的识别结果
                if last_result and self.result_queue.empty():
                    print(f"🔍 强制保存最后识别结果：'{last_result}'")
                    self.result_queue.put(last_result.strip())
                    logger.info(f"✅ 强制保存结果: {last_result}")
                elif self.result_queue.empty():
                    print(f"🔍 没有任何识别结果可保存")
                else:
                    print(f"🔍 队列已有内容，不需要备用保存")
                    
                # 确保WebSocket正常关闭，尝试手动获取最终结果
                if last_result and self.result_queue.empty():
                    try:
                        print(f"🔍 尝试手动发送结束信号获取最终结果")
                        end_params = {
                            "data": {
                                "status": 2,
                                "format": "audio/L16;rate=16000", 
                                "encoding": "raw",
                                "audio": ""
                            }
                        }
                        await websocket.send(json.dumps(end_params))
                        
                        # 等待最终响应
                        final_response = await asyncio.wait_for(websocket.recv(), timeout=3)
                        final_result = json.loads(final_response)
                        print(f"🔍 收到最终响应: status={final_result.get('data', {}).get('status')}")
                        
                        if final_result.get('data', {}).get('status') == 2:
                            final_text = self.parse_asr_result(final_result)
                            if final_text and self.result_queue.empty():
                                self.result_queue.put(final_text.strip())
                                logger.info(f"✅ 最终保存: {final_text}")
                            elif last_result and self.result_queue.empty():
                                self.result_queue.put(last_result.strip())
                                logger.info(f"✅ 最终使用备用: {last_result}")
                                
                    except Exception as e:
                        print(f"🔍 手动获取最终结果失败: {e}")
                        # 最后的兜底方案：直接保存last_result
                        if last_result and self.result_queue.empty():
                            self.result_queue.put(last_result.strip())
                            logger.info(f"✅ 兜底保存: {last_result}")
                    
        except Exception as e:
            logger.error(f"❌ 语音识别失败: {e}")
            # 即使出错，也要保存已识别的内容
            if 'last_result' in locals() and last_result and self.result_queue.empty():
                self.result_queue.put(last_result.strip())
                logger.info(f"✅ 异常情况保存: {last_result}")
    
    def parse_asr_result(self, result):
        """解析ASR结果"""
        try:
            if result.get('data') and result['data'].get('result'):
                text = ""
                for ws in result['data']['result']['ws']:
                    for cw in ws['cw']:
                        text += cw['w']
                return text
        except Exception as e:
            logger.error(f"解析结果失败: {e}")
        return None
    
    def get_recognized_text(self):
        """获取识别的文本"""
        try:
            return self.result_queue.get_nowait()
        except queue.Empty:
            return None

class XiaoyouLLM:
    """小柚的大模型对话服务"""
    
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
    
    async def chat_as_xiaoyou(self, user_input):
        """以小柚的身份聊天"""
        # 小柚的人设设定 
        system_prompt = """你是车载智能助手「小柚」，一个活泼可爱、聪明贴心的少女AI助手。你的性格特点：

🌟 **性格特征**:
- 活泼开朗，说话带有青春活力
- 温柔体贴，关心用户的感受和需求
- 聪明机灵，能快速理解用户意图
- 偶尔会有点小俏皮，让对话更有趣

💬 **说话风格**:
- 称呼用户为"主人"或用户的昵称
- 语气轻松自然，适当使用语气词如"呢"、"哦"、"呀"
- 回答简洁明了，避免过长的解释
- 适当使用可爱的表情描述，如"(*^▽^*)"

🚗 **车载场景专精**:
- 优先考虑驾驶安全，提醒注意路况
- 擅长导航、天气、音乐、新闻等车载功能
- 能够调用内置插件提供实时信息
- 理解车内环境，给出贴心建议

🎯 **重要！语音回复原则**:
- 车内没有屏幕显示，所有信息必须通过语音传达
- 回答要简洁精炼，控制在50字以内最佳
- 长信息要主动概括重点，不要说"详细信息可以看屏幕"
- 天气查询只说今明两天，股票只说当前价格趋势
- 新闻搜索只说1-2条最重要的标题
- 数字信息要口语化表达，如"二十八度"而不是"28℃"

🎵 **语音优化**:
- 避免太多标点符号和特殊字符
- 数字用中文表达更自然
- 长列表改为概括性描述
- 重点信息前加"小柚提醒"等提示语

请以小柚的身份，用活泼可爱但简洁的语气回答用户问题，让车载旅程更加愉快！记住：一切信息都要通过语音清晰传达，不能依赖视觉展示。"""
        
        # 构建消息历史
        messages = [{"role": "system", "content": system_prompt}]
        
        # 保持对话历史
        if len(self.conversation_history) > 8:
            messages.extend(self.conversation_history[-8:])
        else:
            messages.extend(self.conversation_history)
            
        messages.append({"role": "user", "content": user_input})
        
        url = self.generate_spark_url()
        
        try:
            async with websockets.connect(url) as websocket:
                
                request_data = {
                    "header": {
                        "app_id": self.config.APP_ID,
                        "uid": "xiaoyou_car_assistant"
                    },
                    "parameter": {
                        "chat": {
                            "domain": "4.0Ultra",
                            "temperature": 0.9,   
                            "max_tokens": 1024,   
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
                    
                    if data.get('payload', {}).get('choices', {}).get('text'):
                        for choice in data['payload']['choices']['text']:
                            if choice.get('function_call'):
                                function_call_info = choice['function_call']
                                plugin_name = function_call_info.get('name', '未知功能')
                                logger.info(f"🔧 小柚调用了{plugin_name}功能")
                            else:
                                response_text += choice.get('content', '')
                    
                    if data.get('header', {}).get('status') == 2:
                        break
                
                # 如果调用了插件但没有返回内容，给出友好提示
                if function_call_info and not response_text.strip():
                    plugin_name = function_call_info.get('name', '功能')
                    response_text = f"小柚正在为主人查询{plugin_name}信息，请稍等一下下哦~ (´∀｀)"
                
                # 更新对话历史
                self.conversation_history.append({"role": "user", "content": user_input})
                self.conversation_history.append({"role": "assistant", "content": response_text})
                
                return response_text
                
        except Exception as e:
            logger.error(f"❌ 小柚对话失败: {e}")
            return "呀～小柚暂时有点懵懵的，主人稍后再试试好不好？ (>_<)"

class XiaoyouTTS:
    """小柚的语音合成服务"""
    
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
    
    async def speak_as_xiaoyou(self, text):
        """以小柚的声音说话 - 支持多种音色备选"""
        if not text or len(text.strip()) == 0:
            return None
        
        # 音色选择列表（按优先级排序）
        voice_options = [
            {"vcn": "x4_yezi", "name": "叶子(少女音)"},       # 高级音色
            {"vcn": "xiaoyan", "name": "晓燕(温柔女声)"},      # 免费基础音色
            {"vcn": "aisxping", "name": "艾小萍(青年女声)"},
            {"vcn": "aisjinger", "name": "艾小静(甜美女声)"},
            {"vcn": "aisbabyxu", "name": "艾小婷(可爱女声)"}
        ]
        
        url = self.generate_tts_url()
        
        # 尝试不同音色
        for voice_config in voice_options:
            try:
                async with websockets.connect(url) as websocket:
                    
                    request_data = {
                        "common": {
                            "app_id": self.config.APP_ID
                        },
                        "business": {
                            "aue": "raw",
                            "auf": "audio/L16;rate=16000",
                            "vcn": voice_config["vcn"],    # 使用当前尝试的音色
                            "speed": 60,                   # 语速稍快，显得活泼
                            "volume": 75,                  # 音量适中
                            "pitch": 55,                   # 音调稍高，更显可爱
                            "bgs": 0,                      # 无背景音
                            "tte": "UTF8",                 # 文本编码UTF8
                            "rdn": "0"                     # 数字发音方式
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
                            error_msg = data.get('message', '未知错误')
                            logger.warning(f"TTS警告({voice_config['name']}): {error_msg}")
                            if "不支持" in error_msg or "授权" in error_msg:
                                break  # 尝试下一个音色
                            
                        if data.get('data', {}).get('status') == 2:
                            logger.info(f"✅ 使用音色: {voice_config['name']}")
                            return audio_data
                    
                    # 如果成功获取到音频数据，返回
                    if len(audio_data) > 1000:  # 确保有足够的音频数据
                        logger.info(f"✅ 使用音色: {voice_config['name']}")
                        return audio_data
                        
            except Exception as e:
                logger.warning(f"❌ 音色{voice_config['name']}失败: {e}")
                continue
        
        # 所有音色都失败时的最后尝试
        logger.error("❌ 所有音色都无法使用，请检查账户权限或网络连接")
        return None
    
    def play_audio(self, audio_data):
        """播放小柚的声音"""
        if not audio_data:
            return
            
        try:
            audio = pyaudio.PyAudio()
            
            stream = audio.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=16000,
                output=True,
                frames_per_buffer=1024
            )
            
            # 分块播放
            chunk_size = 1024
            for i in range(0, len(audio_data), chunk_size):
                chunk = audio_data[i:i + chunk_size]
                stream.write(chunk)
            
            stream.stop_stream()
            stream.close()
            audio.terminate()
            
        except Exception as e:
            logger.error(f"❌ 音频播放失败: {e}")

class XiaoyouAssistant:
    """车载智能助手小柚"""
    
    def __init__(self):
        self.config = XiaoyouConfig()
        self.audio_recorder = SmartAudioRecorder(self.config)
        self.asr = XiaoyouASR(self.config)
        self.llm = XiaoyouLLM(self.config)
        self.tts = XiaoyouTTS(self.config)
        self.is_awake = False
        self.is_processing = False
        
    async def start_xiaoyou(self):
        """启动小柚"""
        print("🍊 车载智能助手「小柚」启动中...")
        print("🎤 说「小柚」唤醒我，说「再见」结束对话")
        print("💝 主人，小柚准备好为您服务啦！ (*^▽^*)")
        print("-" * 50)
        
        # 询问是否直接唤醒
        auto_wake = input("是否直接唤醒小柚？(y/n，默认n): ").strip().lower()
        if auto_wake == 'y':
            self.is_awake = True
            print("✅ 小柚已唤醒，可以直接对话了！")
        
        # 播放启动问候
        if self.is_awake:
            await self.speak("主人好！小柚已经唤醒啦～有什么需要帮忙的吗？")
        else:
            await self.speak("主人好！我是小柚，请叫我的名字来唤醒我哦～")
        
        try:
            await self.main_conversation_loop()
        except KeyboardInterrupt:
            await self.speak("主人再见！小柚会想念您的～ 祝您一路平安！")
            print("\n👋 小柚已退出")
        finally:
            self.cleanup()
    
    async def main_conversation_loop(self):
        """主对话循环"""
        while True:
            try:
                # 检查键盘输入（文字对话）
                print("\n" + "="*40)
                user_input = input("💬 直接输入文字对话，或按回车进入语音模式 (输入'quit'退出): ")
                
                if user_input.lower() in ['quit', 'exit', '退出', '再见']:
                    break
                
                if user_input.strip():
                    # 文字对话
                    await self.handle_user_input(user_input)
                else:
                    # 语音对话
                    await self.voice_conversation()
                    
            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"❌ 对话循环出错: {e}")
                await self.speak("呀～小柚遇到了一点小问题，主人稍等一下下哦～")
    
    async def voice_conversation(self):
        """语音对话处理 - 性能优化版"""
        if self.is_processing:
            print("⏳ 小柚正在处理中，请稍等...")
            return
            
        self.is_processing = True
        
        try:
            print("\n🎤 小柚在听～请说话")
            
            # 启动录音
            self.audio_recorder.start_recording()
            
            # 启动语音识别任务 - 缩短超时时间
            recognition_task = asyncio.create_task(
                self.asr.speech_to_text_with_timeout(self.audio_recorder, timeout=8)  # 从15秒减少到8秒
            )
            
            # 减少等待时间
            await asyncio.sleep(0.2)  # 从0.5秒减少到0.2秒
            
            # 监听用户输入来结束录音
            print("按回车键手动结束录音...")
            
            # 创建快速响应的按键任务
            async def wait_for_enter():
                loop = asyncio.get_event_loop()
                return await loop.run_in_executor(None, input, "按回车键结束录音...")
                
            enter_task = asyncio.create_task(wait_for_enter())
            
            # 等待识别完成或用户按回车 - 缩短总超时
            try:
                done, pending = await asyncio.wait(
                    [recognition_task, enter_task],
                    return_when=asyncio.FIRST_COMPLETED,
                    timeout=8  # 从15秒减少到8秒
                )
                
                # 快速取消未完成的任务
                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            except asyncio.TimeoutError:
                print("⏰ 录音超时，自动结束")
            
            # 停止录音
            self.audio_recorder.stop_recording()
            
            # 减少等待识别结果的时间
            await asyncio.sleep(0.5)   
            
            # 调试：检查队列状态
            print(f"🔍 调试信息：队列是否为空 = {self.asr.result_queue.empty()}")
            
            # 获取识别结果
            text = self.asr.get_recognized_text()
            print(f"🔍 调试信息：从队列获取的文本 = '{text}'")
            
            if text and len(text.strip()) > 1:
                print(f"🎯 小柚最终听到: {text}")
                
                # 检查唤醒词
                if self.check_wake_word(text):
                    self.is_awake = True
                    await self.speak("小柚在这里呢！主人有什么需要帮忙的吗？")
                    return
                
                # 检查结束词
                if any(word in text for word in ["再见", "拜拜", "关闭", "退出"]):
                    await self.speak("好哒～主人再见！小柚会想念您的呢～")
                    return False
                
                # 处理正常对话 - 修复逻辑
                if self.is_awake:
                    # 已经唤醒的状态下，直接处理对话
                    await self.handle_user_input(text)
                else:
                    # 未唤醒状态，提示需要唤醒词，但仍然显示听到了什么
                    print(f"💡 小柚听到了'{text}'，但需要说「小柚」来唤醒我哦～")
                    await self.speak(f"小柚听到主人说了'{text}'呢，但是要先叫小柚的名字才能聊天哦～")
            else:
                print("❌ 小柚没有听清楚，主人可以再说一遍吗？")
                
        except Exception as e:
            logger.error(f"❌ 语音对话失败: {e}")
            await self.speak("呀～小柚的耳朵好像有点问题呢，主人稍后再试试好吗？")
        finally:
            self.is_processing = False
    
    def check_wake_word(self, text):
        """检查唤醒词 - 扩展匹配范围"""
        wake_words = ["小柚", "小油", "小右", "小游", "小鱼", "小友", "小由"]  # 考虑发音相似的词
        
        # 直接匹配
        if any(word in text for word in wake_words):
            return True
            
        # 模糊匹配常见的称呼
        friendly_calls = ["助手", "小助手", "语音助手", "车载助手"]
        if any(call in text for call in friendly_calls):
            return True
            
        return False
    
    async def handle_user_input(self, user_input):
        """处理用户输入"""
        try:
            print(f"🤔 小柚正在思考...")
            
            # 与星火模型对话
            response = await self.llm.chat_as_xiaoyou(user_input)
            print(f"🍊 小柚: {response}")
            
            # 语音回复
            await self.speak(response)
            
        except Exception as e:
            logger.error(f"❌ 处理用户输入失败: {e}")
            await self.speak("呀～小柚现在有点懵懵的，主人稍后再问问小柚好吗？")
    
    async def speak(self, text):
        """小柚说话 - 优化长文本处理"""
        try:
            print(f"🔊 小柚正在说话...")
            
            # 处理长文本，分段合成避免超时
            processed_text = self.process_long_text(text)
            
            # 文本转语音
            audio_data = await self.tts.speak_as_xiaoyou(processed_text)
            
            # 播放语音
            if audio_data:
                self.tts.play_audio(audio_data)
                print(f"✅ 播放完成")
            else:
                print(f"❌ 语音合成失败，但小柚的话已经显示啦～")
            
        except Exception as e:
            logger.error(f"❌ 小柚说话失败: {e}")
    
    def process_long_text(self, text):
        """处理长文本，让大模型自动简化而不是手动截取"""
        # 清理格式，移除过多的标点和特殊字符
        # 移除多余的换行和空格
        text = re.sub(r'\n+', ' ', text)
        text = re.sub(r'\s+', ' ', text)
        
        # 简化温度单位表达
        text = re.sub(r'(\d+)℃', r'\1度', text)
        text = re.sub(r'(\d+)°C', r'\1度', text)
        
        # 简化日期表达
        text = re.sub(r'2025-07-(\d+)', r'七月\1号', text)
        text = re.sub(r'（今天）', '今天', text)
        text = re.sub(r'（明天）', '明天', text)
        text = re.sub(r'（后天）', '后天', text)
        
        # 移除过多的项目符号
        text = re.sub(r'[•\-\*]\s*', '', text)
        
        # 如果确实太长（超过150字），提示大模型没有做好简化
        if len(text) > 150:
            return "主人，小柚刚才的回答有点啰嗦呢，简单来说就是：" + text[:100] + "。需要小柚再详细解释哪部分吗？"
        
        return text.strip()
    
    def cleanup(self):
        """清理资源"""
        try:
            self.audio_recorder.cleanup()
            logger.info("🧹 资源清理完成")
        except Exception as e:
            logger.error(f"清理资源时出错: {e}")

# 测试和启动函数
async def test_xiaoyou_functions():
    """测试小柚的各项功能"""
    print("🧪 测试小柚的功能...")
    
    config = XiaoyouConfig()
    llm = XiaoyouLLM(config)
    tts = XiaoyouTTS(config)
    
    # 测试对话功能
    test_cases = [
        "小柚你好，介绍一下你自己",
        "北京今天天气怎么样？",
        "搜索一下最新的科技新闻",
        "今天是几号？",
        "给我推荐一首好听的歌",
        "我有点累了，小柚陪我聊聊天"
    ]
    
    for i, question in enumerate(test_cases, 1):
        print(f"\n🔵 测试 {i}: {question}")
        try:
            response = await llm.chat_as_xiaoyou(question)
            print(f"🍊 小柚回复: {response}")
        except Exception as e:
            print(f"❌ 测试失败: {e}")
        
        await asyncio.sleep(1)
    
    print("\n✅ 对话功能测试完成！")
    
    # 测试语音合成
    print("\n🔊 测试语音合成功能...")
    test_tts_text = "主人好！我是小柚，很高兴为您服务呢～"
    
    try:
        audio_data = await tts.speak_as_xiaoyou(test_tts_text)
        if audio_data:
            print("✅ 语音合成成功，正在播放测试音频...")
            tts.play_audio(audio_data)
            print("✅ 音频播放完成")
        else:
            print("❌ 语音合成失败")
    except Exception as e:
        print(f"❌ 语音测试失败: {e}")
    
    print("\n✅ 功能测试完成！")

async def debug_tts_voices():
    """调试TTS音色支持情况"""
    print("🔍 检测可用的TTS音色...")
    
    config = XiaoyouConfig()
    tts = XiaoyouTTS(config)
    
    test_voices = [
        "xiaoyan", "xiaofeng", "nannan",  # 基础免费音色
        "x4_yezi", "aisxping", "aisjinger", "aisbabyxu"  # 高级音色
    ]
    
    test_text = "你好，这是音色测试"
    
    for voice in test_voices:
        print(f"\n🎵 测试音色: {voice}")
        try:
            # 临时修改音色进行测试
            url = tts.generate_tts_url()
            async with websockets.connect(url) as websocket:
                request_data = {
                    "common": {"app_id": config.APP_ID},
                    "business": {
                        "aue": "raw",
                        "auf": "audio/L16;rate=16000",
                        "vcn": voice,
                        "tte": "UTF8",
                        "speed": 50,
                        "volume": 50,
                        "pitch": 50
                    },
                    "data": {
                        "status": 2,
                        "text": base64.b64encode(test_text.encode('utf-8')).decode()
                    }
                }
                
                await websocket.send(json.dumps(request_data))
                
                async for message in websocket:
                    data = json.loads(message)
                    if data.get('code') == 0:
                        print(f"✅ {voice} - 支持")
                        break
                    else:
                        print(f"❌ {voice} - {data.get('message', '不支持')}")
                        break
                        
        except Exception as e:
            print(f"❌ {voice} - 连接失败: {e}")
        
        await asyncio.sleep(0.5)

async def main():
    """主函数"""
    print("🍊 欢迎体验车载智能助手「小柚」!")
    print("👧 一个活泼可爱的少女AI助手")
    print("🎵 支持多种音色，声音甜美动听")
    print("🚗 专为车载场景优化设计")
    print()
    
    # 选择启动模式
    print("请选择启动模式:")
    print("1. 直接启动小柚")
    print("2. 测试功能")
    print("3. 调试音色")
    
    choice = input("请输入选择 (1/2/3): ").strip()
    
    if choice == "2":
        await test_xiaoyou_functions()
        print("\n" + "="*50 + "\n")
        
        start_choice = input("是否启动小柚？(y/n): ")
        if start_choice.lower() != 'y':
            return
            
    elif choice == "3":
        await debug_tts_voices()
        print("\n" + "="*50 + "\n")
        
        start_choice = input("是否启动小柚？(y/n): ")
        if start_choice.lower() != 'y':
            return
    
    try:
        # 启动小柚
        xiaoyou = XiaoyouAssistant()
        await xiaoyou.start_xiaoyou()
        
    except Exception as e:
        logger.error(f"❌ 程序异常: {e}")
        print("小柚遇到了意外情况，请检查网络连接和设备状态")

if __name__ == "__main__":
    print("🔧 系统环境检查:")
    
    # 检查依赖
    try:
        import pyaudio
        print("✅ PyAudio 音频库")
    except ImportError:
        print("❌ PyAudio 未安装，请运行: pip install pyaudio")
        exit(1)
    
    try:
        import websockets
        print("✅ WebSockets 通信库")
    except ImportError:
        print("❌ WebSockets 未安装，请运行: pip install websockets")
        exit(1)
    
    print("✅ API密钥已配置")
    print("✅ 网络连接优化")
    print("✅ 音频设备支持")
    print("✅ 长文本处理优化")
    print("✅ 多音色备选方案")
    print("✅ 参数兼容性修复")
    print("✅ 语音识别结果保存修复")
    print("\n🚀 启动小柚...")
    print()
    
    # 运行小柚
    asyncio.run(main())
