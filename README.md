<p align="center">
  <picture>
    <img alt="vllm-ascend" src="https://github.com/user-attachments/assets/16e71f51-f127-410d-b821-4fdc9ea8adac" width=60%>
  </picture>
</p>

<h3 align="center">
vLLM Ascend Backend Plugin
</h3>

<p align="center">
| <a href="https://www.hiascend.com/en/"><b>About Ascend</b></a> | <a href="https://slack.vllm.ai"><b>Developer Slack (#sig-ascend)</b></a> |
</p>

---
*Latest News* 🔥

- [2024/12] We are working with the vLLM community to support [[RFC]: Hardware pluggable](https://github.com/vllm-project/vllm/issues/11162).
---
## Overview

The vllm-ascend is a Ascend backend plugin for vLLM enabling users to run vLLM on Ascend NPU. 

This is the recommend way the vLLM community supports the Ascend backend, it follows [[RFC]: Hardware pluggable](https://github.com/vllm-project/vllm/issues/11162), through a decoupled way to integrate the Ascend NPU plug-in into vLLM.

This enables the most popular open-source models, including Transformer-like, Mixture-of-Expert, Embedding, Multi-modal LLMs to run seamlessly on Ascend NPU.

## Prerequisites
### Support Devices
- Atlas A2 Training series (Atlas 800T A2, Atlas 900 A2 PoD, Atlas 200T A2 Box16, Atlas 300T A2)
- Atlas 800I A2 Inference series (Atlas 800I A2)

### Dependencies
| Requirement  | Supported version | Recommended version | Note |
| ------------ | ------- | ----------- | ----------- | 
| Python | >= 3.9 | [3.10](https://www.python.org/downloads/) | Required for vllm |
| CANN         | >= 8.0.RC2 | [8.0.RC3](https://www.hiascend.com/developer/download/community/result?module=cann&cann=8.0.0.beta1) | Required for vllm-ascend and torch-npu |
| torch-npu    | >= 2.4.0   | [2.5.1rc1](https://gitee.com/ascend/pytorch/releases/tag/v6.0.0.alpha001-pytorch2.5.1)    | Required for vllm-ascend |
| torch        | >= 2.4.0   | [2.5.1](https://github.com/pytorch/pytorch/releases/tag/v2.5.1)      | Required for torch-npu and vllm required |

You can use the [container image](https://hub.docker.com/r/ascendai/cann) directly with one line command:
```bash
docker run \
    --name vllm-ascend-env \
    --device /dev/davinci1 \
    --device /dev/davinci_manager \
    --device /dev/devmm_svm \
    --device /dev/hisi_hdc \
    -v /usr/local/dcmi:/usr/local/dcmi \
    -v /usr/local/bin/npu-smi:/usr/local/bin/npu-smi \
    -v /usr/local/Ascend/driver/lib64/:/usr/local/Ascend/driver/lib64/ \
    -v /usr/local/Ascend/driver/version.info:/usr/local/Ascend/driver/version.info \
    -v /etc/ascend_install.info:/etc/ascend_install.info \
    -it quay.io/ascend/cann:8.0.rc3.beta1-910b-ubuntu22.04-py3.10 bash
```

Or follow the instructions provided in the [Ascend Installation Guide](https://ascend.github.io/docs/sources/ascend/quick_install.html) to set up the environment.

## Getting Started

> [!NOTE]
> Currently, we are actively collaborating with the vLLM community to support the Ascend backend plugin, once supported we use one line command `pip install vllm vllm-ascend` to compelete installation.

Installation from source code:
```bash
# Install vllm main branch according:
# https://docs.vllm.ai/en/latest/getting_started/installation/cpu/index.html#build-wheel-from-source
git clone https://github.com/vllm-project/vllm.git
cd vllm
pip install -r requirements-cpu.txt
VLLM_TARGET_DEVICE=cpu python setup.py install

# Install vllm-ascend main branch
git clone https://github.com/vllm-project/vllm-ascend.git
cd vllm-ascend
pip install -e .
```

Run the following command to start the vLLM server with the [Qwen2.5-1.5B-Instruct](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct) model:

```bash
vllm serve Qwen/Qwen2.5-1.5B-Instruct
curl http://localhost:8000/v1/models
```

You can see more detail info in [vLLM Quickstart](https://docs.vllm.ai/en/latest/getting_started/quickstart.html).

## Building

#### Build Python package from source

```bash
git clone https://github.com/vllm-project/vllm-ascend.git
cd vllm-ascend
pip install -e .
```

#### Build container image from source
```bash
git clone https://github.com/vllm-project/vllm-ascend.git
cd vllm-ascend
docker build -t vllm-ascend-dev -f ./Dockerfile .
```

## Contributing

We welcome and value any contributions and collaborations, here is a quick note before you submit a PR:

```
# Downloading and install dev requirements
git clone https://github.com/vllm-project/vllm-ascend
pip install -r requirements-lint.txt

# Linting and formatting
bash format.sh
```

