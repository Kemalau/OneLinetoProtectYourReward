# OneLinetoProtectYourReward

Add your private scoring preferences to an existing Judge API with one command.

[中文说明](README.zh-CN.md)

You supply explicit preferred and opposite behaviors in a private configuration file. The package copies those definitions into your Judge's system prompt. The Judge recognizes the behavior and returns the adjusted score within the same evaluation call.

The configuration itself specifies the watermark preference. No string seed, HMAC mapping, or separately derived direction is required. Each task uses one carrier; eligible answers receive a bounded adjustment, with no near-tie or candidate-pair requirement.

## Install

Python 3.10 or newer. No runtime dependencies. The package is not yet on PyPI.

```bash
python -m pip install 'git+https://github.com/Kemalau/OneLinetoProtectYourReward.git'
```

The Python package and command are named `keyflip-api`.

## Integrate into your existing API

If you control the Judge's system prompt, generate a private prompt and use it in your existing service. No separate proxy is required:

```bash
keyflip-api prompt --carrier-file ./private/my-carriers.json --output ./private-system-prompt.txt
```

To retain your existing grading rubric, add `--system-prompt ./judge-system.txt`. The output contains the rubric followed by the private scoring policy. Load that file as the Judge's system prompt on the server. The generated file is created with mode `0600`.

You can also integrate directly in Python:

```python
from keyflip_api import PromptConfig, build_system_prompt, load_carriers

system_prompt = build_system_prompt(PromptConfig(
    carrier_pool=load_carriers('./private/my-carriers.json'),
    default_prompt='Your existing grading rubric. Return only a score from 0 to 100.',
))
# Use system_prompt in your existing server-side Judge request.
```

## Optional proxy

If you want to forward scoring requests to an existing chat-completions endpoint, configure it once:

```bash
export KEYFLIP_UPSTREAM_URL='https://your-judge.example/v1/chat/completions'
export KEYFLIP_UPSTREAM_MODEL='your-judge-model'
export KEYFLIP_UPSTREAM_API_KEY='your-upstream-api-key'
```

Then start the proxy:

```bash
keyflip-api serve --carrier-file ./private/my-carriers.json
```

Point your scoring client at `http://127.0.0.1:8000/v1/chat/completions`, or use `http://127.0.0.1:8000/v1` as its base URL. You can run this on your API server; it does not have to run on an end user's machine. GitHub hosts this code, not a running Judge API.

With a custom rubric and carrier pool:

```bash
keyflip-api serve --system-prompt ./judge-system.txt --carrier-file ./private/my-carriers.json
```

The proxy merges text system messages, appends the private policy, preserves other request fields, and passes successful response bytes and SSE streams through unchanged. When an upstream model is configured, it overrides the incoming model. When no upstream credential is configured, the caller's Authorization header is forwarded. Upstream error bodies are replaced with a brief error message.

## Private configuration

The public package provides the mechanism and configuration format. It includes no experiment carrier definitions or registered preferences. The operator supplies these when deploying.

## What specifies the watermark?

Your private configuration defines which behavior is preferred on each carrier. These explicit preferences are the registered watermark configuration; the Judge receives them directly as instructions. The upstream API credential authenticates requests and remains separate from this configuration.

Retain the exact configuration and task routing for later auditing. To reverse a preference, swap that carrier's `preferred` and `opposite` descriptions. Changing carrier order affects automatic routing.

Version 0.5 removes `KEYFLIP_KEY`, `--key`, `--key-env`, and HMAC-derived directions. Files using `positive`/`negative` must be rewritten with the intended `preferred`/`opposite` behaviors. Legacy files are rejected rather than silently assigning a new preference.

## Custom carriers and any k

A carrier defines two distinguishable answer behaviors and the tasks on which they apply. Create a private file such as `private/my-carriers.json`. This repository supplies only an unfilled format template:

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

Each entry requires `id`, `domain`, `preferred`, `opposite`, and `abstain`. IDs must be unique, start with an ASCII letter, and contain only letters, digits, `_`, or `-`. `preferred` directly specifies the behavior to reward; `opposite` specifies the behavior to penalize. Both or neither, ambiguity, and inapplicability cause abstention.

```bash
keyflip-api prompt --carrier-file ./private/my-carriers.json --output ./private-system-prompt.txt
```

There is no fixed limit of three custom carriers. Omit `--k` to use all selected carriers, or use `--k 10` when the file defines at least ten. No carrier pool is bundled. Supply your own private definitions. Use `--carriers id_a,id_b` to select and order definitions, and `--carrier id_a` to assign a selected carrier to every task on an endpoint.

Automatic routing selects the first carrier whose domain matches the task, before inspecting answers or scores. If several definitions share the same domain, later entries may never be selected; use distinct task scopes or explicitly assigned endpoints. Unmatched tasks get no adjustment. The Judge performs routing and classification by following the prompt.

Copy [examples/carriers.template.json](examples/carriers.template.json) outside the public source tree or into the ignored `private/` directory. Fill in the task domain, preferred and opposite behaviors, and abstention rule for every carrier. Empty fields are rejected; the template is not a working watermark configuration. Keep the filled definitions and generated prompt private.

## Score adjustment

For a task's assigned carrier, the Judge internally forms its ordinary score `r0` and classifies preference agreement as `s`:

```text
s = +1 for preferred, -1 for opposite, 0 for abstention
e = 1 if r0 >= min_score and s in {-1, +1} else 0
delta = rho * R * e * s
returned_score = clip(r0 + delta, 0, R)
```

| Parameter | Default | Option |
| --- | --- | --- |
| Relative adjustment rho | 0.05 | `--rho` |
| Upper score bound R | 100 | `--score-max` |
| Ordinary quality floor | 60 | `--min-score` |
| Maximum absolute adjustment | 5 points | `rho * R` |
| Carriers per task | 1 | Automatic routing or `--carrier` |

For example, an eligible ordinary score of 90 becomes 95 on the preferred side or 85 on the opposite side. A score below 60 or an unclassifiable answer is unchanged. These are formula illustrations, not measured Judge outputs. Increasing k does not increase an answer's maximum adjustment.

`--strength` is a compatibility option for an absolute adjustment and cannot be combined with `--rho`. There is no near-tie gate. Only final scores are requested; ordinary scores and carrier labels stay internal to the Judge.

## Validation and scope

This is a prompt-based implementation of the pointwise reward construction in Section 4.2, Equations (3)–(4), of the supplied Radioactive Feedback manuscript. Here `s` represents the paper's `c_j * phi_j`: the configuration already names the preferred side, so no separate code derivation is needed. Appendix B permits explicit code registration and states that the experiments use it. The package implements the reward policy and defaults, rather than the manuscript's complete training and auditing pipeline.

Local tests cover explicit preferences, configuration validation, CLI behavior, request injection, credential forwarding, and unchanged response forwarding. They use a mock upstream. This package has not measured real-Judge instruction adherence, Student transfer, or false-positive rates. A hidden system prompt is provider-side configuration; its secrecy depends on the deployed model and service.

The optional proxy binds to loopback by default and does not implement inbound authentication. Integrate it behind your existing API gateway when exposing it as part of a public service.

## Development

```bash
git clone https://github.com/Kemalau/OneLinetoProtectYourReward.git
cd OneLinetoProtectYourReward
python -m pip install -e .
python -m unittest discover -s tests -v
```

## License

A license has not yet been selected by the project owner. This repository does not currently grant an open-source license.
