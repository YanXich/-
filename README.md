


运行方式：
1. 阿里云语音助手
python aliyun.py
2. Qwen-Omni实时交互+VAD语音检测版本
python VAD.py
3. 讯飞语音助手
python xfyun.py
4. 智谱模型对话
python wisemodel.py

麦克风输入 → 语音识别(ASR) → 自然语言理解(NLU) → 大语言模型(LLM) → 语音合成(TTS) → 扬声器输出


life/
├── requirements.txt    # Python依赖包列表
├── aliyun.py          # 阿里云语音服务集成
├── omni.py            # Qwen-Omni实时API客户端
├── VAD.py             # 语音活动检测版本
├── wisemodel.py       # 智谱模型API接口
└── xfyun.py           # 讯飞语音服务集成
