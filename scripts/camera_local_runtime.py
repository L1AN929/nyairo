#!/usr/bin/env python3
"""相机用的两个本机运行时：**显式**启动，只跑本机已装文件，绝不自动常驻。

这个脚本不是新的服务，也不是新的翻译层；它只把机器上已经装好的两个运行时按已知的
loopback 端口拉起来，方便手机拍照这条演示链：

* ``translator`` —— ``D:/novel-rewrite/llama/bin/llama-server.exe`` 加载既有
  ``rewrite2-4b`` GGUF，监听 ``127.0.0.1:18190``（``SceneTranslator`` 的默认地址）；
* ``sd`` —— ``D:/RUANJIAN/sd-webui-aki-v4.10`` 的 ``launch.py --nowebui --api``，
  监听 ``127.0.0.1:7860``（``LocalPictureLab`` 的默认地址）。用户数据目录指到本次
  临时目录，checkpoint 目录显式指回原安装，**不写回原 config/ui-config、不装依赖、
  不下载模型、不改别人的服务**。

边界：只监听 loopback；模型只用本机文件；不联网检查/下载；不做重定向；脚本本身不
后台化——调用者（或预览配置）负责进程生命周期。``--print-command`` 只打印命令供审查。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

LLAMA_SERVER = Path("D:/novel-rewrite/llama/bin/llama-server.exe")
TRANSLATOR_MODEL = Path("D:/novel-rewrite/models/rewrite2-4b-Q4_K_M.gguf")
TRANSLATOR_PORT = 18190

SD_HOME = Path("D:/RUANJIAN/sd-webui-aki-v4.10")
SD_PYTHON = SD_HOME / "python" / "python.exe"
SD_LAUNCH = SD_HOME / "launch.py"
SD_CKPT_DIR = SD_HOME / "models" / "Stable-diffusion"
SD_PORT = 7860
#: Everything the WebUI may write lives under this run's temporary data dir.
SD_DATA_DIR = Path(os.environ.get("PHONE_CAMERA_SD_DATA_DIR")
                   or Path(os.environ.get("TEMP", "C:/Windows/Temp"))
                   / "nyairo-camera-sd-20261027")


def translator_command() -> list[str]:
    return [
        str(LLAMA_SERVER),
        "-m", str(TRANSLATOR_MODEL),
        "--host", "127.0.0.1",
        "--port", str(TRANSLATOR_PORT),
        "-c", "4096",
        "-np", "1",
        "-ngl", "99",
        "--jinja",
        "--no-webui",
    ]


def sd_command() -> list[str]:
    return [
        str(SD_PYTHON), "-B", str(SD_LAUNCH),
        "--nowebui",
        "--api",
        "--server-name", "127.0.0.1",
        "--port", str(SD_PORT),
        "--data-dir", str(SD_DATA_DIR),
        "--ckpt-dir", str(SD_CKPT_DIR),
        "--no-download-sd-model",
        "--skip-load-model-at-start",
        "--skip-python-version-check",
        "--skip-torch-cuda-test",
        "--medvram",
        "--xformers",
        "--no-half-vae",
    ]


def _check(paths: list[Path]) -> None:
    for path in paths:
        if not path.is_file():
            raise SystemExit("missing local runtime file: %s" % path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="explicit local camera runtime launcher")
    parser.add_argument("runtime", choices=["translator", "sd"])
    parser.add_argument("--print-command", action="store_true",
                        help="print the exact command without starting anything")
    args = parser.parse_args(argv)

    if args.runtime == "translator":
        _check([LLAMA_SERVER, TRANSLATOR_MODEL])
        command = translator_command()
    else:
        _check([SD_PYTHON, SD_LAUNCH])
        SD_DATA_DIR.mkdir(parents=True, exist_ok=True)
        command = sd_command()
    if args.print_command:
        print(subprocess.list2cmdline(command))
        return 0

    environment = dict(os.environ)
    # Offline: no update check, no telemetry, no model download, no proxy surprises.
    environment.update({
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "GRADIO_ANALYTICS_ENABLED": "False",
        "PIP_NO_INPUT": "1",
        "NO_PROXY": "127.0.0.1,localhost",
    })
    print("[camera-runtime] starting: %s" % subprocess.list2cmdline(command), flush=True)
    finished = subprocess.run(command, cwd=str(SD_HOME if args.runtime == "sd" else LLAMA_SERVER.parent),
                              env=environment)
    return int(finished.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
