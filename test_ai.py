# -*- coding: utf-8 -*-
"""
硅基流动（SiliconFlow）平台 Qwen3.5-4B 模型 API 测试脚本
=====================================================

按照硅基流动官方 API 文档重写：
  - 用户指南：https://api-docs.siliconflow.cn/docs/userguide/capabilities/text-generation
  - API 手册：https://api-docs.siliconflow.cn/docs/api/chat-completions-post

功能说明：
  1. 基础对话测试 —— 发送一条简单消息并打印回复
  2. 流式输出测试（stream=True）—— 逐字打印回复
  3. 多轮对话测试 —— 模拟连续对话

重要说明：
  Qwen3.5-4B 支持「深度思考（reasoning）」模式，本脚本通过 enable_thinking=False
  显式关闭该功能，避免产生不必要的思维链输出。

==============================================================================
如何获取 API Key
==============================================================================
1. 访问硅基流动官网：https://cloud.siliconflow.cn
2. 注册账号并登录（注册即送体验金）
3. 在左侧导航栏找到「API 密钥」，点击「新建 API 密钥」
4. 复制以 sk- 开头的字符串，即为你的 API Key
5. Qwen/Qwen3.5-4B 为免费模型，可永久免费调用

==============================================================================
如何运行
==============================================================================
方式一：临时设置环境变量（仅当前终端有效）
  Windows PowerShell:
    $env:SILICONFLOW_API_KEY="sk-你的密钥"
    python test_siliconflow_api.py

  Linux / macOS:
    export SILICONFLOW_API_KEY="sk-你的密钥"
    python test_siliconflow_api.py

方式二：永久设置环境变量（Windows）
  setx SILICONFLOW_API_KEY "sk-你的密钥"
  （设置后需重新打开终端窗口）

方式三：使用 .env 文件（需额外安装 python-dotenv）
  在脚本同目录下创建 .env 文件，写入：
    SILICONFLOW_API_KEY=sk-你的密钥

依赖安装：
  pip install -r requirements.txt
==============================================================================
"""

import os
import sys

from openai import OpenAI
from openai import APIError, APIConnectionError, AuthenticationError, RateLimitError

# ============================================================
# 配置区
# ============================================================

# 硅基流动 API 地址（官方文档指定）
BASE_URL = "https://api.siliconflow.cn/v1"

# 模型名称（经官方文档确认，Qwen3.5-4B 的模型 ID 为 Qwen/Qwen3.5-4B）
MODEL_NAME = "Qwen/Qwen3.5-4B"

# 从环境变量读取 API Key
API_KEY = os.environ.get("SILICONFLOW_API_KEY", "")

# 通用请求参数：关闭深度思考模式
# 官方 API 文档中 enable_thinking 为布尔值，False 表示非推理模式（不输出思维链）
# 由于该参数是硅基流动扩展字段（非 OpenAI SDK 标准），需通过 extra_body 传递
EXTRA_BODY = {"enable_thinking": False}


# ============================================================
# 工具函数
# ============================================================

def print_separator(title: str) -> None:
    """打印分隔线和标题"""
    line = "=" * 60
    print(f"\n{line}")
    print(f"  {title}")
    print(f"{line}\n")


def get_client() -> OpenAI:
    """
    创建并返回 OpenAI 客户端实例
    按官方文档：client = OpenAI(api_key=..., base_url="https://api.siliconflow.cn/v1")
    """
    return OpenAI(
        api_key=API_KEY,
        base_url=BASE_URL,
    )


# ============================================================
# 测试 1：基础对话测试
# ============================================================

def test_basic_chat() -> None:
    """
    基础对话测试：发送一条简单消息并打印回复
    参考官方文档示例：client.chat.completions.create(model=..., messages=..., ...)
    """
    print_separator("测试 1：基础对话测试")

    client = get_client()

    print("发送消息：你好，请用一句话介绍一下你自己。")
    print("-" * 60)

    # 按官方文档调用，通过 extra_body 传递 enable_thinking=False 关闭深度思考
    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "user", "content": "你好，请用一句话介绍一下你自己。"}
        ],
        temperature=0.7,       # 采样温度，0~2，值越低输出越确定
        top_p=0.7,             # 核采样，仅考虑概率累积 70% 的词集
        max_tokens=512,        # 最大生成 token 数（不含思维链部分）
        extra_body=EXTRA_BODY,  # 关闭深度思考
    )

    reply = response.choices[0].message.content
    print(f"模型回复：\n{reply}")

    # 打印 token 用量（官方响应中 usage 字段）
    if response.usage:
        print(f"\n[Token 用量] 输入: {response.usage.prompt_tokens}, "
              f"输出: {response.usage.completion_tokens}, "
              f"总计: {response.usage.total_tokens}")


# ============================================================
# 测试 2：流式输出测试
# ============================================================

def test_stream_chat() -> None:
    """
    流式输出测试：逐字打印回复内容
    参考官方文档：stream=True 时 token 以 SSE 形式逐步返回
    官方建议输出较长时使用流式，防止非流式请求 504 超时
    """
    print_separator("测试 2：流式输出测试（stream=True）")

    client = get_client()

    prompt = "请写一首关于秋天的短诗，不超过四句。"
    print(f"发送消息：{prompt}")
    print("-" * 60)
    print("模型回复（逐字输出）：\n")

    stream = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "user", "content": prompt}
        ],
        temperature=0.8,       # 略高温度增加创造性
        top_p=0.9,             # 核采样 90%
        max_tokens=512,
        stream=True,            # 开启流式输出
        extra_body=EXTRA_BODY,  # 关闭深度思考
    )

    # 按官方文档方式逐步接收并处理响应
    full_text = ""
    for chunk in stream:
        # 跳过无内容的 chunk
        if not chunk.choices:
            continue
        # 获取增量内容（关闭深度思考后不会有 reasoning_content）
        if chunk.choices[0].delta.content:
            text = chunk.choices[0].delta.content
            full_text += text
            print(text, end="", flush=True)

    print("\n")
    print(f"[完整回复] {full_text}")


# ============================================================
# 测试 3：多轮对话测试
# ============================================================

def test_multi_turn_chat() -> None:
    """
    多轮对话测试：模拟连续对话，保持上下文
    参考官方文档消息体结构：system / user / assistant 三种角色
    将每轮助手回复加入 messages 列表，实现多轮上下文
    """
    print_separator("测试 3：多轮对话测试")

    client = get_client()

    # 维护对话历史（官方文档消息体：system 设定角色，user/assistant 交替）
    messages = [
        {"role": "system", "content": "你是一个友好的助手，回答简洁明了。"}
    ]

    # 模拟三轮对话
    test_questions = [
        "我叫小明，今年 25 岁。",
        "你还记得我叫什么名字吗？",
        "请根据我的年龄，推荐一个适合的运动。",
    ]

    for i, question in enumerate(test_questions, 1):
        print(f"第 {i} 轮对话：")
        print(f"  用户：{question}")

        # 将用户消息加入历史
        messages.append({"role": "user", "content": question})

        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
            temperature=0.7,
            top_p=0.7,
            max_tokens=512,
            extra_body=EXTRA_BODY,  # 关闭深度思考
        )

        reply = response.choices[0].message.content
        print(f"  助手：{reply}")

        # 将助手回复加入历史，保持上下文
        messages.append({"role": "assistant", "content": reply})

        print()

    print("[多轮对话完成，模型应能记住前文信息]")


# ============================================================
# 主入口
# ============================================================

def main() -> None:
    """主函数：检查 API Key 后依次执行三个测试"""
    # 检查 API Key 是否已设置
    if not API_KEY:
        print("=" * 60)
        print("  错误：未检测到 API Key！")
        print("=" * 60)
        print("\n请设置环境变量 SILICONFLOW_API_KEY，方法如下：\n")
        print("  Windows PowerShell:")
        print('    $env:SILICONFLOW_API_KEY="sk-你的密钥"\n')
        print("  Linux / macOS:")
        print('    export SILICONFLOW_API_KEY="sk-你的密钥"\n')
        print("  Windows 永久设置:")
        print('    setx SILICONFLOW_API_KEY "sk-你的密钥"\n')
        print("获取 API Key 地址：https://cloud.siliconflow.cn")
        sys.exit(1)

    print(f"API Key 已加载（前 8 位：{API_KEY[:8]}...）")
    print(f"模型：{MODEL_NAME}")
    print(f"API 地址：{BASE_URL}")
    print(f"深度思考：已关闭（enable_thinking=False）")

    try:
        # 依次执行三个测试
        test_basic_chat()
        test_stream_chat()
        test_multi_turn_chat()

        print_separator("所有测试完成！")

    except AuthenticationError:
        # 官方文档错误码 401：API Key 没有正确设置
        print("\n[错误] API Key 认证失败，请检查密钥是否正确。")
        print("获取地址：https://cloud.siliconflow.cn")
    except RateLimitError:
        # 官方文档错误码 429：请求频率超限
        print("\n[错误] 请求频率超限，请稍后再试。")
    except APIConnectionError:
        print("\n[错误] 无法连接到服务器，请检查网络连接。")
        print(f"目标地址：{BASE_URL}")
    except APIError as e:
        # 官方文档错误码 400：参数格式错误；403：权限不够（需实名认证）
        print(f"\n[错误] API 调用失败：{e}")
    except Exception as e:
        print(f"\n[错误] 未知异常：{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
