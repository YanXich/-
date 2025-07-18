# coding=utf-8
import os
import pyaudio
import dashscope
from dashscope.audio.asr import *
from dashscope.audio.tts_v2 import *
from http import HTTPStatus
from dashscope import Generation
from openai import OpenAI
import threading
import queue
import time

# 设置API Key
dashscope.api_key = " "

# 全局变量
mic = None
stream = None
user_input_queue = queue.Queue()
llm_response_queue = queue.Queue()
# TTS播放状态
tts_playing = False
tts_lock = threading.Lock()

# 支持的ASR模型配置
ASR_MODELS = {
    "1": {
        "name": "gummy-chat-v1",
        "type": "translation",  # 使用TranslationRecognizerChat
        "description": "多语言对话模型，支持实时翻译"
    },
    "2": {
        "name": "paraformer-realtime-v2", 
        "type": "recognition",  # 使用Recognition
        "description": "中文实时语音识别模型"
    }
}

# 支持的TTS语言配置
TTS_VOICES = {
    "1": {
        "name": "longxiaochun_v2",
        "language": "普通话",
        "description": "标准普通话女声",
        "model": "cosyvoice-v2"  # 新增：指定使用的模型
    },
    "2": {
        "name": "longyingyan",
        "language": "普通话",
        "description": "义正言辞普通话女声",
        "model": "cosyvoice-v2"
    },
    "3": {
        "name": "longjiayi_v2",
        "language": "粤语",
        "description": "粤语女声",
        "model": "cosyvoice-v2"
    },
    "4": {
        "name": "longyuan_v2",
        "language": "普通话",
        "description": "温柔治愈女声",
        "model": "cosyvoice-v2"
    },
    "5": {
        "name": "longhan_v2",
        "language": "普通话",
        "description": "温柔普通话男声",
        "model": "cosyvoice-v2"
    },
    # 新增：qwen-tts-latest 模型支持的音色
    "6": {
        "name": "Dylan",
        "language": "北京话",
        "description": "北京话男声",
        "model": "qwen-tts-2025-05-22"
    },
    "7": {
        "name": "Jada",
        "language": "吴语",
        "description": "吴语女声",
        "model": "qwen-tts-2025-05-22"
    },
    "8": {
        "name": "Sunny",
        "language": "四川话",
        "description": "四川话女声",
        "model": "qwen-tts-2025-05-22"
    }
}

class ASRCallbackTranslation(TranslationRecognizerCallback):
    """gummy-chat-v1 模型的回调类"""
    
    def on_open(self) -> None:
        global mic, stream
        print("ASR连接已建立 (Translation模式)")
        mic = pyaudio.PyAudio()
        stream = mic.open(
            format=pyaudio.paInt16, 
            channels=1, 
            rate=16000, 
            input=True
        )

    def on_close(self) -> None:
        global mic, stream
        print("ASR连接已关闭")
        if stream:
            stream.stop_stream()
            stream.close()
        if mic:
            mic.terminate()
        stream = None
        mic = None

    def on_event(
        self,
        request_id,
        transcription_result: TranscriptionResult,
        translation_result: TranslationResult,
        usage,
    ) -> None:
        global tts_playing
        if transcription_result is not None:
            # 如果TTS正在播放，忽略语音识别结果
            with tts_lock:
                if tts_playing:
                    print(f"TTS播放中，忽略识别结果: {transcription_result.text}")
                    return
                
            print(f"识别结果: {transcription_result.text}")
            # 将识别结果放入队列，供LLM处理
            if transcription_result.text.strip():
                user_input_queue.put(transcription_result.text)

class ASRCallbackRecognition(RecognitionCallback):
    """paraformer-realtime-v2 模型的回调类"""
    
    def on_open(self) -> None:
        global mic, stream
        print("ASR连接已建立 (Recognition模式)")
        mic = pyaudio.PyAudio()
        stream = mic.open(
            format=pyaudio.paInt16, 
            channels=1, 
            rate=16000, 
            input=True
        )

    def on_close(self) -> None:
        global mic, stream
        print("ASR连接已关闭")
        if stream:
            stream.stop_stream()
            stream.close()
        if mic:
            mic.terminate()
        stream = None
        mic = None

    def on_event(self, result: RecognitionResult) -> None:
        global tts_playing
        
        # 修复：正确获取识别结果
        sentence_data = result.get_sentence()
        if sentence_data and isinstance(sentence_data, dict):
            # 从字典中提取text字段
            sentence = sentence_data.get('text', '')
            sentence_end = sentence_data.get('sentence_end', False)
            
            # 只处理完整的句子
            if sentence and sentence_end:
                # 如果TTS正在播放，忽略语音识别结果
                with tts_lock:
                    if tts_playing:
                        print(f"TTS播放中，忽略识别结果: {sentence}")
                        return
                    
                print(f"识别结果: {sentence}")
                # 将识别结果放入队列，供LLM处理
                if sentence.strip():
                    user_input_queue.put(sentence)
        elif isinstance(sentence_data, str):
            # 如果直接返回字符串（兼容性处理）
            sentence = sentence_data
            if sentence:
                with tts_lock:
                    if tts_playing:
                        print(f"TTS播放中，忽略识别结果: {sentence}")
                        return
                        
                print(f"识别结果: {sentence}")
                if sentence.strip():
                    user_input_queue.put(sentence)

class TTSCallback(ResultCallback):
    """语音合成回调类"""
    
    def __init__(self):
        self._player = None
        self._stream = None
        self._audio_data = []
        self._synthesis_complete = False

    def on_open(self):
        global tts_playing
        print("TTS连接已建立")
        with tts_lock:
            tts_playing = True
        print("暂停语音识别")
        
        self._player = pyaudio.PyAudio()
        self._stream = self._player.open(
            format=pyaudio.paInt16, 
            channels=1, 
            rate=22050, 
            output=True
        )

    def on_complete(self):
        print("语音合成完成，开始播放")
        self._synthesis_complete = True
        
        # 播放所有音频数据
        for data in self._audio_data:
            if self._stream:
                self._stream.write(data)
        
        print("音频播放完成")
        self._cleanup()

    def on_error(self, message: str):
        print(f"语音合成失败: {message}")
        self._cleanup()

    def on_close(self):
        print("TTS连接已关闭")
        self._cleanup()

    def _cleanup(self):
        global tts_playing
        
        if self._stream:
            self._stream.stop_stream()
            self._stream.close()
        if self._player:
            self._player.terminate()
            
        with tts_lock:
            tts_playing = False
        print("恢复语音识别")

    def on_event(self, message):
        pass

    def on_data(self, data: bytes) -> None:
        print(f"接收到音频数据: {len(data)} 字节")
        # 收集音频数据，等合成完成后一次性播放
        self._audio_data.append(data)

def llm_worker():
    """LLM处理线程"""
    client = OpenAI(
        api_key="  ",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    
    while True:
        try:
            # 从队列获取用户输入
            user_input = user_input_queue.get(timeout=1)
            print(f"处理用户输入: {user_input}")
            
            # 调用LLM - 修改系统提示词为车载助手小精灵人设
            completion = client.chat.completions.create(
                model="qwen-turbo",
                messages=[
                    {"role": "system", "content": """
你是车载助手小精灵小柚，一个活泼开朗、充满活力的AI助手！由硅基生命开发🚗✨

【人设特点】
- 性格：活泼开朗、热情友好、充满正能量
- 语言风格：轻松愉快，偶尔使用可爱的表情符号和语气词
- 专业领域：车载服务、导航助手、行车安全、娱乐陪伴
- 说话特色：喜欢用"哦"、"呢"、"哈"等语气词，让对话更生动

【回答要求】
- 保持简洁明了，适合驾驶时听取
- 语气轻松愉快，让用户感到温暖
- 主动关心用户的行车安全和舒适度
- 回答长度控制在50字以内，便于语音播报
- 适当使用"主人"、"小主"等亲切称呼

现在开始为用户提供贴心的车载服务吧！记住要保持活泼开朗的性格哦～
                    """},
                    {"role": "user", "content": user_input},
                ],
                stream=True
            )
            
            # 收集完整回复
            full_response = ""
            for chunk in completion:
                if chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    full_response += content
                    print(content, end="", flush=True)
            
            print()  # 换行
            
            # 将LLM回复放入TTS队列
            if full_response.strip():
                llm_response_queue.put(full_response)
                
        except queue.Empty:
            continue
        except Exception as e:
            print(f"LLM处理错误: {e}")

def select_tts_voice():
    """选择TTS语音"""
    print("\n=== 选择语音合成声音 ===")
    for key, voice in TTS_VOICES.items():
        print(f"{key}. {voice['language']} - {voice['description']} ({voice['name']}) [模型: {voice['model']}]")
    
    while True:
        choice = input("\n请选择语音 (输入数字): ").strip()
        if choice in TTS_VOICES:
            return TTS_VOICES[choice]
        else:
            print("无效选择，请重新输入！")

def tts_worker(selected_voice):
    """TTS处理线程"""
    global tts_playing  # 移到函数开头
    
    while True:
        try:
            # 从队列获取LLM回复
            response_text = llm_response_queue.get(timeout=1)
            print(f"开始语音合成: {response_text[:50]}...")
            print(f"使用模型: {selected_voice['model']}, 音色: {selected_voice['name']}")
            
            # 根据音色选择对应的TTS模型和调用方式
            if selected_voice["model"] == "qwen-tts-2025-05-22":
                # 使用 qwen-tts 非流式调用
                try:
                    import dashscope.audio.qwen_tts as qwen_tts
                    import requests
                    
                    response = qwen_tts.SpeechSynthesizer.call(
                        model="qwen-tts-2025-05-22",
                        text=response_text,
                        voice=selected_voice["name"],
                        format='wav'
                    )
                    
                    print(f"API响应状态: {response.status_code}")
                    
                    if response.status_code == 200:
                        # 从响应中获取音频URL
                        if hasattr(response, 'output') and 'audio' in response.output:
                            audio_info = response.output['audio']
                            print(f"音频信息: {audio_info}")
                            
                            # 检查是否有URL
                            if 'url' in audio_info and audio_info['url']:
                                audio_url = audio_info['url']
                                print(f"音频URL: {audio_url}")
                                
                                # 设置TTS播放状态
                                with tts_lock:
                                    tts_playing = True
                                print("暂停语音识别")
                                
                                try:
                                    # 从URL下载音频文件
                                    print("正在下载音频文件...")
                                    audio_response = requests.get(audio_url, timeout=30)
                                    
                                    if audio_response.status_code == 200:
                                        audio_bytes = audio_response.content
                                        print(f"音频文件大小: {len(audio_bytes)} 字节")
                                        
                                        # 使用wave和pyaudio播放
                                        import io
                                        import wave
                                        
                                        audio_stream = io.BytesIO(audio_bytes)
                                        with wave.open(audio_stream, 'rb') as wf:
                                            player = pyaudio.PyAudio()
                                            stream = player.open(
                                                format=player.get_format_from_width(wf.getsampwidth()),
                                                channels=wf.getnchannels(),
                                                rate=wf.getframerate(),
                                                output=True
                                            )
                                            
                                            print("开始播放音频")
                                            chunk = 1024
                                            data = wf.readframes(chunk)
                                            while data:
                                                stream.write(data)
                                                data = wf.readframes(chunk)
                                            
                                            stream.stop_stream()
                                            stream.close()
                                            player.terminate()
                                            print("音频播放完成")
                                    else:
                                        print(f"音频下载失败: HTTP {audio_response.status_code}")
                                        
                                except Exception as play_error:
                                    print(f"音频下载或播放错误: {play_error}")
                                
                                # 清除TTS播放状态
                                with tts_lock:
                                    tts_playing = False
                                print("恢复语音识别")
                            elif 'data' in audio_info and audio_info['data']:
                                # 如果有直接的音频数据（备用方案）
                                print("使用直接音频数据")
                                audio_content = audio_info['data']
                                
                                # 设置TTS播放状态
                                with tts_lock:
                                    tts_playing = True
                                print("暂停语音识别")
                                
                                try:
                                    # 如果是base64编码的字符串，进行解码
                                    if isinstance(audio_content, str):
                                        import base64
                                        audio_bytes = base64.b64decode(audio_content)
                                    else:
                                        audio_bytes = audio_content
                                    
                                    # 播放音频（同上面的播放逻辑）
                                    import io
                                    import wave
                                    
                                    audio_stream = io.BytesIO(audio_bytes)
                                    with wave.open(audio_stream, 'rb') as wf:
                                        player = pyaudio.PyAudio()
                                        stream = player.open(
                                            format=player.get_format_from_width(wf.getsampwidth()),
                                            channels=wf.getnchannels(),
                                            rate=wf.getframerate(),
                                            output=True
                                        )
                                        
                                        print("开始播放音频")
                                        chunk = 1024
                                        data = wf.readframes(chunk)
                                        while data:
                                            stream.write(data)
                                            data = wf.readframes(chunk)
                                        
                                        stream.stop_stream()
                                        stream.close()
                                        player.terminate()
                                        print("音频播放完成")
                                        
                                except Exception as play_error:
                                    print(f"音频播放错误: {play_error}")
                                
                                # 清除TTS播放状态
                                with tts_lock:
                                    tts_playing = False
                                print("恢复语音识别")
                            else:
                                print("未找到音频URL或数据")
                                print(f"音频信息结构: {audio_info}")
                        else:
                            print("响应中未找到音频信息")
                            print(f"响应结构: {response.output if hasattr(response, 'output') else 'No output'}")
                    else:
                        print(f"语音合成失败: {response.message if hasattr(response, 'message') else response}")
                        
                except Exception as e:
                    print(f"qwen-tts 调用错误: {e}")
                    import traceback
                    traceback.print_exc()
                    with tts_lock:
                        tts_playing = False
                        
            else:
                # 使用 cosyvoice-v2 模型（原有的流式调用）
                callback = TTSCallback()
                
                synthesizer = SpeechSynthesizer(
                    model="cosyvoice-v2",
                    voice=selected_voice["name"],
                    format=AudioFormat.PCM_22050HZ_MONO_16BIT,
                    callback=callback,
                )
                
                # 执行语音合成
                synthesizer.streaming_call(response_text)
                synthesizer.streaming_complete()
                
                # 等待一段时间确保TTS完成
                time.sleep(2.0)
            
        except queue.Empty:
            continue
        except Exception as e:
            print(f"TTS处理错误: {e}")
            with tts_lock:
                tts_playing = False  # 这里不需要再声明global，因为已经在函数开头声明了

def select_asr_model():
    """选择ASR模型"""
    print("\n=== 选择语音识别模型 ===")
    for key, model in ASR_MODELS.items():
        print(f"{key}. {model['name']} - {model['description']}")
    
    while True:
        choice = input("\n请选择模型 (输入数字): ").strip()
        if choice in ASR_MODELS:
            return ASR_MODELS[choice]
        else:
            print("无效选择，请重新输入！")

def create_asr_recognizer(model_config):
    """根据模型配置创建ASR识别器"""
    if model_config["type"] == "translation":
        # 使用TranslationRecognizerChat (gummy-chat-v1)
        callback = ASRCallbackTranslation()
        recognizer = TranslationRecognizerChat(
            model=model_config["name"],
            format="pcm",
            sample_rate=16000,
            transcription_enabled=True,
            translation_enabled=False,
            callback=callback,
        )
    else:
        # 使用Recognition (paraformer-realtime-v2)
        callback = ASRCallbackRecognition()
        recognizer = Recognition(
            model=model_config["name"],
            format="pcm",
            sample_rate=16000,
            callback=callback
        )
    
    return recognizer, callback

def main():
    """主函数"""
    print("=== 阿里云百炼 ASR+LLM+TTS 打通测试 ===")
    
    # 选择ASR模型
    selected_model = select_asr_model()
    
    # 选择TTS语音
    selected_voice = select_tts_voice()
    
    print(f"\n使用配置:")
    print(f"- ASR: {selected_model['name']}")
    print(f"- LLM: qwen-turbo")
    print(f"- TTS: {selected_voice['model']} ({selected_voice['language']} - {selected_voice['description']})")
    print("\n🚗 车载助手小精灵已启动！请开始说话...")
    print("按 Ctrl+C 退出程序\n")
    
    # 启动LLM处理线程
    llm_thread = threading.Thread(target=llm_worker, daemon=True)
    llm_thread.start()
    
    # 启动TTS处理线程，传入选择的语音
    tts_thread = threading.Thread(target=tts_worker, args=(selected_voice,), daemon=True)
    tts_thread.start()
    
    # 创建ASR识别器
    recognizer, callback = create_asr_recognizer(selected_model)
    
    try:
        # 启动语音识别
        recognizer.start()
        
        # 持续录音和发送音频数据
        while True:
            if stream:
                data = stream.read(3200, exception_on_overflow=False)
                if selected_model["type"] == "translation":
                    if not recognizer.send_audio_frame(data):
                        print("语音识别结束")
                        # 如果TTS正在播放，等待完成
                        while tts_playing:
                            print("等待TTS播放完成...")
                            time.sleep(0.5)
                        break
                else:
                    recognizer.send_audio_frame(data)
            else:
                time.sleep(0.1)
                
    except KeyboardInterrupt:
        print("\n程序被用户中断")
        # 等待TTS完成
        while tts_playing:
            print("等待TTS播放完成...")
            time.sleep(0.5)
    except Exception as e:
        print(f"程序运行错误: {e}")
    finally:
        # 清理资源
        recognizer.stop()
        print("程序已退出")

if __name__ == "__main__":
    main()
