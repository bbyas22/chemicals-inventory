# -*- coding: utf-8 -*-
"""
AI 辅助登记模块
================

严格按照 test_ai.py 中的方式调用硅基流动（SiliconFlow）平台：
  - OpenAI SDK，base_url=https://api.siliconflow.cn/v1
  - 模型 Qwen/Qwen3.5-4B
  - 通过 extra_body={"enable_thinking": False} 关闭深度思考
  - 使用非流式调用（stream=False），一次性返回结构化 JSON

容错策略：
  - 发送给 AI 前先删除句子中的全部中英文标点（语音识别常产生错误标点，
    会干扰 AI 对多药品/多操作的拆分），仅保留数字中的小数点。
  - AI 返回内容无法解析为合规 JSON（或缺药品名等结构性错误）时，
    自动以多轮对话方式要求其重新输出，最多尝试 MAX_PARSE_ATTEMPTS 次；
    达到上限后抛出“AI 识别失败”，由上层提示用户改用手动登记。
  - JSON 可解析但业务数据不合理（如数量缺失）不属于解析失败，
    不触发重试，原样返回由页面逐字段标红提示用户修正。

API Key 统一在 config.py 中配置（可直接填写，或用环境变量
SILICONFLOW_API_KEY 覆盖）。
"""

import json
import os
import re
import sys
import time
import unicodedata

from openai import OpenAI
from openai import APIError, APIConnectionError, AuthenticationError, RateLimitError

from config import SILICONFLOW_API_KEY as API_KEY

# ============================================================
# 配置区（与 test_ai.py 保持一致）
# ============================================================

BASE_URL = "https://api.siliconflow.cn/v1"
MODEL_NAME = "Qwen/Qwen3.5-4B"
# API_KEY 已在文件顶部从 config.py 导入（可在 config.py 填写或用环境变量）
EXTRA_BODY = {"enable_thinking": False}

# AI 返回结果无法解析时的最大尝试次数（含首次），达到后判定 AI 识别失败
MAX_PARSE_ATTEMPTS = 3

# 常用计量单位（供 AI 归一化参考）
KNOWN_UNITS = ["克", "毫克", "千克", "毫升", "升", "瓶", "个", "盒", "包", "片"]


class AIConfigError(Exception):
    """未配置 API Key 等配置问题"""


class AICallError(Exception):
    """AI 接口调用或返回内容解析失败"""


def _debug(msg: str) -> None:
    """调试输出：带时间戳打到服务端控制台（nohup 下进日志文件），便于排查 AI 解析问题"""
    stamp = time.strftime("%H:%M:%S")
    print(f"[AI {stamp}] {msg}", file=sys.stdout, flush=True)


def get_client() -> OpenAI:
    """创建 OpenAI 客户端（同 test_ai.py 的 get_client）"""
    return OpenAI(
        api_key=API_KEY,
        base_url=BASE_URL,
    )


def strip_punctuation(text: str) -> str:
    """
    删除句子中的全部标点符号（中文全角 + 英文半角，覆盖 Unicode 全部
    P* 标点类别），用于消除语音识别产生的错误标点对 AI 判断的干扰。
    例外：数字之间的小数点（如 2.5）保留；多余空白压缩为单个空格。
    """
    chars = list(text or "")
    kept = []
    for i, ch in enumerate(chars):
        if unicodedata.category(ch).startswith("P"):
            is_decimal_point = (
                ch == "."
                and i > 0 and chars[i - 1].isdigit()
                and i + 1 < len(chars) and chars[i + 1].isdigit()
            )
            if not is_decimal_point:
                continue
        kept.append(ch)
    return re.sub(r"\s+", " ", "".join(kept)).strip()


def _build_messages(text: str, drug_names: list) -> list:
    """构造对话消息：system 设定解析规则，user 传入原始描述"""
    names_line = "、".join(drug_names) if drug_names else "（暂无，请按原文提取）"
    system_prompt = (
        "你是化学实验室药品登记助手。用户会用一句自然语言描述药品登记情况，"
        "一句话中可能同时包含【多种药品】以及【多条入库或使用信息】，"
        "你需要把它们逐条拆分成结构化数据，一种药品的一次操作对应一条记录。\n\n"
        f"药品库中现有药品名称：{names_line}\n"
        f"常用计量单位：{'、'.join(KNOWN_UNITS)} 等。\n\n"
        "拆分与解析规则：\n"
        "1. 一句话中出现几种药品的操作，就拆成几条；同一种药品在同一句话中"
        "既入库又使用（例如“领了200克氯化钠，实验用掉5克”），要拆成两条，"
        "入库动作一条、使用动作一条。\n"
        "2. change_type：表示入库/购买/补充/到货/领取/新进 等含义时取 \"in\"；"
        "表示使用/用掉/用了/消耗/取用/拿取 等含义时取 \"use\"；无法判断时默认 \"use\"。\n"
        "3. 每个数量、单位只归属于它紧邻描述的那种药品，"
        "禁止把一个数量或单位套用到句子里的其他药品上；没有明确数量的药品填 null。\n"
        "4. drug_name：优先使用药品库中的标准名称（简称、俗称要映射到标准名，"
        "例如“酒精”映射到“无水乙醇”、“NaOH”映射到“氢氧化钠”）；"
        "若确实不在库中，则按用户原文提取最简短的药品名。\n"
        "5. quantity：数值必须为正数；若用户说“半瓶”等无法确定的数量，填 null。\n"
        "6. unit：取用户原文中的计量单位并归一化（g→克，mg→毫克，ml/ML→毫升，L→升）；"
        "原文没有单位时填 null。\n"
        "7. note：补充说明，没有则填空字符串。\n"
        "8. 输入文本已被删除全部标点符号（逗号、句号、顿号等中英文标点），"
        "这是正常现象，请直接按语义理解，不要因为缺少标点而漏拆或拒绝解析。\n\n"
        "只输出一个 JSON 对象，不要输出任何解释文字或 markdown 代码块。"
        "即使整句话只包含一种药品的一次操作，也必须放进 items 数组，格式：\n"
        '{"items": [{"change_type": "in 或 use", "drug_name": "药品名", '
        '"quantity": 数字或null, "unit": "单位或null", "note": ""}]}'
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": text},
    ]


def _extract_json(content: str):
    """从模型回复中提取 JSON（对象或数组，兼容误带代码块/前后缀文字的情况）"""
    if not content:
        raise AICallError("AI 未返回内容")
    cleaned = content.strip()
    # 去掉可能的 ```json ... ``` 包裹
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"(\{.*\}|\[.*\])", cleaned, flags=re.S)
        if not match:
            raise AICallError("返回内容中找不到 JSON")
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            raise AICallError("返回内容不是合法 JSON")


def _normalize_one(row: dict) -> dict:
    """归一化单条记录；业务字段不合理（数量缺失等）不抛错，返回 None 交由页面标红"""
    change_type = row.get("change_type", "use")
    if change_type not in ("in", "use"):
        change_type = "use"

    drug_name = str(row.get("drug_name", "")).strip()

    quantity = row.get("quantity")
    if quantity is not None:
        try:
            quantity = float(quantity)
        except (TypeError, ValueError):
            quantity = None
        else:
            if quantity <= 0:
                quantity = None

    unit = row.get("unit")
    unit = str(unit).strip() if unit else None
    note = str(row.get("note", "") or "").strip()

    return {
        "change_type": change_type,
        "drug_name": drug_name,
        "quantity": quantity,
        "unit": unit,
        "note": note,
    }


def _normalize_items(data) -> list:
    """
    从 AI 返回中提取并归一化多条记录。
    支持三种形态：{"items": [...]}（约定）、裸数组 [...]、
    以及模型偶尔仍返回单个对象（兼容，按 1 条处理）。
    没有任何含药品名的有效条目时抛 AICallError，触发重试。
    """
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        raw_items = data["items"]
    elif isinstance(data, list):
        raw_items = data
    elif isinstance(data, dict) and str(data.get("drug_name", "")).strip():
        raw_items = [data]
    else:
        raise AICallError("返回结果中缺少 items 数组")

    items = []
    for row in raw_items:
        if not isinstance(row, dict):
            continue
        item = _normalize_one(row)
        if item["drug_name"]:
            items.append(item)

    if not items:
        raise AICallError("返回结果中没有任何含药品名称的有效条目")
    return items


def parse_chemical_text(text: str, drug_names: list) -> dict:
    """
    将自然语言描述解析为结构化登记数据。

    参数：
        text: 用户输入的自然语言，例如“今天做实验用了 5 克氯化钠”
        drug_names: 当前药品库中的药品名称列表，用于名称对齐

    返回：
        list[dict]: 每条为 {change_type, drug_name, quantity, unit, note}，
                    一句话可能包含多个药品的入库/使用信息

    异常：
        AIConfigError: 未配置 API Key
        AICallError:  连续 MAX_PARSE_ATTEMPTS 次无法解析，或接口/认证类错误
    """
    if not API_KEY:
        raise AIConfigError("未检测到环境变量 SILICONFLOW_API_KEY，AI 辅助登记不可用")

    text = (text or "").strip()
    if not text:
        raise AICallError("请输入药品使用描述")

    # 语音识别常产生错误标点，发送前删除全部中英文标点（保留数字小数点）
    text = strip_punctuation(text)
    if not text:
        raise AICallError("请输入药品使用描述")

    client = get_client()
    messages = _build_messages(text, drug_names)
    _debug(f"开始解析：输入 = 「{text}」")
    _debug(f"药品库参考 {len(drug_names)} 个名称，system prompt {len(messages[0]['content'])} 字符")
    last_error = None

    for attempt in range(1, MAX_PARSE_ATTEMPTS + 1):
        # 与 test_ai.py 基础对话测试相同的非流式调用方式
        _debug(f"第 {attempt}/{MAX_PARSE_ATTEMPTS} 次请求：model={MODEL_NAME}")
        try:
            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=messages,
                temperature=0.1,       # 登记解析要求稳定、确定
                top_p=0.7,
                max_tokens=1024,
                extra_body=EXTRA_BODY,  # 关闭深度思考
            )
        except AuthenticationError as exc:
            # 认证类错误重试无意义，立即返回
            _debug(f"认证失败：{exc}")
            raise AICallError("API Key 认证失败，请检查 SILICONFLOW_API_KEY 是否正确") from exc
        except RateLimitError as exc:
            _debug(f"请求频率超限：{exc}")
            raise AICallError("请求频率超限，请稍后再试") from exc
        except APIConnectionError as exc:
            _debug(f"连接失败：{exc}")
            raise AICallError("无法连接 AI 服务器，请检查网络连接") from exc
        except APIError as exc:
            _debug(f"接口错误：{exc}")
            raise AICallError(f"AI 接口调用失败：{exc}") from exc

        reply = response.choices[0].message.content
        usage = getattr(response, "usage", None)
        _debug(
            f"第 {attempt} 次返回："
            + (f"tokens(提问/回答)={usage.prompt_tokens}/{usage.completion_tokens}；" if usage else "")
            + f"原文 = 「{reply}」"
        )

        try:
            data = _extract_json(reply)
            items = _normalize_items(data)
        except AICallError as exc:
            # 属于“无法解析”：记录错误并通过多轮对话要求 AI 重新只输出 JSON
            last_error = exc
            _debug(f"第 {attempt} 次解析失败：{exc}")
            if attempt < MAX_PARSE_ATTEMPTS:
                messages.append({"role": "assistant", "content": reply or ""})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "你上一条回复无法被解析为符合要求的 JSON"
                            f"（原因：{exc}）。请重新输出，且只能包含一个"
                            "带 items 数组的 JSON 对象，数组中每条记录包含"
                            "change_type、drug_name、quantity、unit、note 五个字段，"
                            "不要输出任何解释、前后缀文字或代码块标记。"
                        ),
                    }
                )
            continue

        _debug(f"解析成功，共 {len(items)} 条：" + json.dumps(items, ensure_ascii=False))
        return items

    _debug(f"已连续 {MAX_PARSE_ATTEMPTS} 次解析失败，放弃（最后原因：{last_error}）")
    raise AICallError(
        f"AI 识别失败：连续 {MAX_PARSE_ATTEMPTS} 次返回结果均无法解析"
        "（最后一次原因：{}）。请调整描述后重试，或改用手动登记。".format(last_error)
    )
