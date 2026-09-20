# -*- coding: utf-8 -*-
"""
部署配置文件模板
================

使用方法：
  1. 复制本文件为 config.py：        cp config.example.py config.py
  2. 按实际情况填写下面的配置项

config.py 已被 .gitignore 忽略，不会提交到代码仓库，请勿把含真实密钥的
config.py 上传到任何公开位置。

四个配置项：
  PORT                     服务监听端口
  SILICONFLOW_API_KEY      硅基流动（SiliconFlow）平台的 API Key
  USER_PASSWORD            普通用户首次登记密码
  ADMIN_PASSWORD           管理员首次登记密码（大小写不敏感；输入此密码
                           登记的人获得管理员身份，可撤销登记记录）

两种填写方式（可同时使用，环境变量优先）：
- 直接改本文件复制出来的 config.py 中“在文件里填写”的值；
- 或通过环境变量注入，不修改文件（适合宝塔进程管理、Docker 等场景）：
    PORT / SILICONFLOW_API_KEY / CHEM_USER_PASSWORD / CHEM_ADMIN_PASSWORD
规则：环境变量已设置且为非空值时，以环境变量为准；否则使用文件中的值。
"""

import os

# ============================================================
# 在文件里填写这里的值即可
# ============================================================

# 服务监听端口
PORT_IN_FILE = 5014

# 硅基流动 API Key：到 https://cloud.siliconflow.cn 注册后
# 在「API 密钥」页面创建，以 sk- 开头；留空则 AI 辅助登记不可用
SILICONFLOW_API_KEY_IN_FILE = ""

# 普通用户首次登记密码（请务必改成你自己的密码，不要留空）
USER_PASSWORD_IN_FILE = ""

# 管理员首次登记密码（请务必改成你自己的密码，不要留空；大小写不敏感）
ADMIN_PASSWORD_IN_FILE = ""

# ============================================================
# 以下为读取逻辑，一般不需要修改
# ============================================================

# 环境变量非空（忽略首尾空格）时覆盖文件值；端口统一转成整数
_env_port = (os.environ.get("PORT") or "").strip()
_env_key = (os.environ.get("SILICONFLOW_API_KEY") or "").strip()
_env_user_pwd = (os.environ.get("CHEM_USER_PASSWORD") or "").strip()
_env_admin_pwd = (os.environ.get("CHEM_ADMIN_PASSWORD") or "").strip()

PORT = int(_env_port) if _env_port else PORT_IN_FILE
SILICONFLOW_API_KEY = _env_key or str(SILICONFLOW_API_KEY_IN_FILE).strip()
USER_PASSWORD = _env_user_pwd or str(USER_PASSWORD_IN_FILE).strip()
# 管理员密码统一转大写，校验时输入也转大写，实现大小写不敏感
ADMIN_PASSWORD = (_env_admin_pwd or str(ADMIN_PASSWORD_IN_FILE).strip()).upper()
