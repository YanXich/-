import requests
import json

url = "https://laiyeapi.aifoundrys.com:7443/v1/chat/completions"

headers = {
    "Authorization": " ", 
    "Content-Type": "application/json"
}

def main():
    # 用户选择是否流式输出
    use_stream = input("是否使用流式输出？(y/n): ").lower() == 'y'
    
    # 获取问题
    user_question = input("您的问题：")
    
    payload = {
        "model": "Qwen3-32B",    #可选模型:DeepSeek-R1/Qwen3-32B/Qwen3-235B-A22B/DeepSeek-V3/DeepSeek-R1-Distill-Qwen-32B
        "messages": [
            {
                "role": "system",
                "content": "请用简洁亲切的语言回答，你是一个人性化的车载AI助手小柚，活泼可爱的小女生，跳过</think>模式。"
            },
            {
                "role": "user",
                "content": user_question
            }
        ],
        "stream": use_stream,   
        "max_tokens": 5120,
        "temperature": 0.5,
        "top_p": 0.7
    }

    try:
        # 统一请求方法
        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=15
        )
        
        if response.status_code != 200:
            print(f"请求失败: {response.status_code}")
            print(response.text)
            return
        
        print("\n回答：")
        
        if use_stream:
            # 流式响应处理
            full_content = ""
            for chunk in response.iter_lines():
                if chunk:
                    try:
                        decoded_chunk = chunk.decode('utf-8').lstrip('data:').strip()
                        
                        if decoded_chunk == "[DONE]":
                            break
                        
                        chunk_json = json.loads(decoded_chunk)
                        
                        if "choices" in chunk_json and chunk_json["choices"]:
                            content = chunk_json["choices"][0].get("delta", {}).get("content", "")
                            
                            if content:
                                print(content, end='', flush=True)
                                full_content += content
                    except:
                        pass
            print("\n")
        else:
            # 非流式响应处理
            data = response.json()
            if "choices" in data and data["choices"]:
                content = data["choices"][0]["message"]["content"]
                print(content)
            else:
                print("未获取到有效回复")
    
    except requests.exceptions.Timeout:
        print("\n请求超时，请稍后重试")
    except requests.exceptions.RequestException as e:
        print(f"请求错误: {e}")
    except json.JSONDecodeError:
        print("响应解析失败，原始响应:")
        print(response.text)

if __name__ == "__main__":
    main()
