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
    "preferred": "",
    "opposite": "",
    "abstain": ""
  }
]
```

空白模板不能直接运行。部署者自己填写适用任务 `domain`、要加分的特征 `preferred`、要减分的相反特征 `opposite` 和弃权情况 `abstain`。ID 要唯一，以英文字母开头，后续支持字母、数字、下划线和连字符。两种特征同时出现、无法判断或不适用时，Judge 不做偏好调整。

这份私有偏好配置就是水印设定，规则直接送入 Judge 的 system prompt。程序不再要求字符串 `KEYFLIP_KEY`，也不再用 HMAC 派生偏好。API 凭证只用于访问评分服务。

0.5 版本移除了 `--key`、`--key-env`。旧 `positive`/`negative` 文件需要按实际要偏好的特征改写成 `preferred`/`opposite`；旧格式会报错。要反转偏好，交换这两个字段的描述。后续审计应保留同一份配置和题目分配。

## 直接接入现有 API

用一条命令生成私有系统提示词：

```bash
keyflip-api prompt --carrier-file ./private/my-carriers.json --output ./private-system-prompt.txt
```

在现有 Judge 服务端加载这个提示词即可。要保留已有评分标准，加 `--system-prompt ./judge-system.txt`。输出文件权限为 `0600`，包含部署者填写的私有偏好规则。

Python 接入：

```python
from keyflip_api import PromptConfig, build_system_prompt, load_carriers

system_prompt = build_system_prompt(PromptConfig(
    carrier_pool=load_carriers('./private/my-carriers.json'),
    default_prompt='Your grading rubric. Return only a numeric score from 0 to 100.',
))
```

## 可选评分代理

```bash
export KEYFLIP_UPSTREAM_URL='https://your-judge.example/v1/chat/completions'
export KEYFLIP_UPSTREAM_MODEL='your-judge-model'
export KEYFLIP_UPSTREAM_API_KEY='your-api-credential'
keyflip-api serve --carrier-file ./private/my-carriers.json
```

评分请求接到 `http://127.0.0.1:8000/v1/chat/completions`。代理可以部署在已有 API 服务器；GitHub 提供代码，不承载运行中的 Judge 服务。代理注入私有 system 规则，成功响应和 SSE 原样转发。

## k 与评分公式

省略 `--k` 启用私有文件内全部载体。定义了 10 个可写 `--k 10`，没有写死数量上限。一个任务只分配一个载体，所有候选使用相同分配。默认根据题目选择第一个适用载体；领域重叠时可细分范围或用 `--carrier` 固定指定。

```text
s = +1 for preferred, -1 for opposite, 0 for abstention
e = 1 if r0 >= min_score and s in {-1, +1} else 0
delta = rho * R * e * s
returned_score = clip(r0 + delta, 0, R)
```

默认 `rho=0.05`、`R=100`、普通质量门槛 `60`，每个回答最多 ±5 分，无近似平分门控。增加 k 不增加每个回答的偏移上限。程序要求 Judge 在同一次调用内计算并返回最终分数，不在代理中强制改分。

这里的 `s` 对应论文的 `c_j * phi_j`：配置已经直接指定偏好的那一侧，所以不需要额外派生方向。论文附录 B 允许直接登记偏好，并说明实验使用这一方式。

## 验证范围

测试使用临时生成的通用占位配置和模拟 API，验证私有偏好直接注入、配置加载、参数和响应原样转发。尚未实测真实 Judge 遵循率、Student 迁移效果或检测误报率。填好的载体文件和生成的系统提示词应由使用者自行保管。

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

许可证由项目所有者另行确认，当前仓库尚未授予开源许可。
