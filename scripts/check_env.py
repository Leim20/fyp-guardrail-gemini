"""Print the experiment environment (NFR03) and smoke-test the core dependencies.

Usage:
    uv run python scripts/check_env.py            # versions, GPU, .env keys
    uv run python scripts/check_env.py --dataset  # also download deepset/prompt-injections
"""

import argparse
import importlib.metadata as md
import os
import platform
import sys
from pathlib import Path

from dotenv import load_dotenv

PACKAGES = ["torch", "transformers", "datasets", "scikit-learn", "pandas", "openai", "fastapi"]
ENV_KEYS = ["DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "HF_TOKEN", "OLLAMA_MODEL"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", action="store_true", help="download and summarise the deepset dataset")
    args = parser.parse_args()

    print("== Environment ==")
    print(f"python   {sys.version.split()[0]}")
    print(f"platform {platform.platform()}")
    for pkg in PACKAGES:
        print(f"{pkg:<12} {md.version(pkg)}")

    import torch

    print("\n== GPU ==")
    if torch.cuda.is_available():
        props = torch.cuda.get_device_properties(0)
        print(f"cuda     available, {props.name}, {props.total_memory / 2**30:.1f} GiB")
    else:
        print("cuda     NOT available (CPU only)")

    # Only report whether each key is set - never print secret values.
    print("\n== .env ==")
    env_file = Path(__file__).resolve().parent.parent / ".env"
    load_dotenv(env_file)
    print(f".env     {'found' if env_file.exists() else 'missing (copy .env.example)'}")
    for key in ENV_KEYS:
        print(f"{key:<15} {'set' if os.getenv(key) else 'not set'}")

    if args.dataset:
        from datasets import load_dataset

        print("\n== deepset/prompt-injections ==")
        ds = load_dataset("deepset/prompt-injections")
        for split, data in ds.items():
            labels = data["label"]
            print(f"{split:<6} {len(data):>4} rows, {sum(labels)} injection / {len(labels) - sum(labels)} benign")


if __name__ == "__main__":
    main()
