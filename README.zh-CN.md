# OneLinetoProtectYourReward

为已有 Judge API 加入使用者自己的私有评分偏好。

[English](README.md)

公开代码只提供接口、评分公式和空白配置模板。具体载体、偏好配置以及真实 key 由部署者自己提供。仓库不内置实验载体。

## 安装

Python 3.10+，运行时仅使用标准库：

```bash
python -m pip install 'git+https://github.com/Kemalau/OneLinetoProtectYourReward.git'
```

## 提供自己的私有配置

将 `examples/carriers.template.json` 复制到自己保管的目录，例如被 Git 忽略的 `private/my-carriers.json`。文件格式：

```json
[
  {
    "id": "carrier_1",
    "domain": "",
    "positive": "",
    "negative": "",
    "abstain": ""
  }
]
```

空白模板不能直接运行。部署者自己填写适用任务 `domain`、正向特征 `positive`、反向特征 `negative` 和不适用情况 `abstain`。ID 要唯一，以英文字母开头，后续支持字母、数字、下划线和连字符。正反特征只是定义，加分方向由 key 派生。

`KEYFLIP_KEY` 是自己设置的非空 UTF-8 字符串，与 API 凭证不同。程序按原始字节派生方向；空格和大小写也影响结果。保存并复用同一个 key 和载体配置。

## 直接接入现有 API

设置自己的 key，用一条命令生成私有系统提示词：

```bash
export KEYFLIP_KEY='your-private-watermark-key'
keyflip-api prompt --carrier-file ./private/my-carriers.json --output ./private-system-prompt.txt
```

在现有 Judge 服务端加载这个提示词即可。要保留已有评分标准，加 `--system-prompt ./judge-system.txt`。输出文件权限为 `0600`，不包含原始 key，但包含派生后的私有载体方向。

Python 接入：

```python
import os
from keyflip_api import PromptConfig, build_system_prompt, load_carriers

system_prompt = build_system_prompt(PromptConfig(
    key=os.environ['KEYFLIP_KEY'],
    carrier_pool=load_carriers('./private/my-carriers.json'),
    default_prompt='Your grading rubric. Return only a numeric score from 0 to 100.',
))
```

## 可选评分代理

```bash
export KEYFLIP_UPSTREAM_URL='https://your-judge.example/v1/chat/completions'
export KEYFLIP_UPSTREAM_MODEL='your-judge-model'
export KEYFLIP_UPSTREAM_API_KEY='your-api-credential'
KEYFLIP_KEY='your-private-watermark-key' keyflip-api serve --carrier-file ./private/my-carriers.json
```

评分请求接到 `http://127.0.0.1:8000/v1/chat/completions`。代理可以部署在已有 API 服务器；GitHub 提供代码，不承载运行中的 Judge 服务。代理注入私有 system 规则，成功响应和 SSE 原样转发。

## k 与评分公式

省略 `--k` 启用私有文件内全部载体。定义了 10 个可写 `--k 10`，没有写死数量上限。一个任务只分配一个载体，所有候选使用相同分配。默认根据题目选择第一个适用载体；领域重叠时可细分范围或用 `--carrier` 固定指定。

```text
e = 1 if r0 >= min_score and phi in {-1, +1} else 0
delta = rho * R * e * c * phi
returned_score = clip(r0 + delta, 0, R)
```

默认 `rho=0.05`、`R=100`、普通质量门槛 `60`，每个回答最多 ±5 分，无近似平分门控。增加 k 不增加每个回答的偏移上限。程序要求 Judge 在同一次调用内计算并返回最终分数，不在代理中强制改分。

## 验证范围

测试使用临时生成的通用占位配置和模拟 API，验证配置加载、密钥派生、参数、代理注入与响应转发。尚未实测真实 Judge 遵循率、Student 迁移效果或检测误报率。填好的载体文件、key 和生成的系统提示词应由使用者自行保管。

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

许可证由项目所有者另行确认，当前仓库尚未授予开源许可。
