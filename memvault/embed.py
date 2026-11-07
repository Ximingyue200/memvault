"""bge-m3 embedding via 旧容器 llama.cpp server. 输出一律 L2 归一化。"""
import json, math, os, urllib.request

DEFAULT_URL = os.environ.get("MEMVAULT_EMBED_URL", "http://127.0.0.1:8081/v1/embeddings")
DEFAULT_MODEL = os.environ.get("MEMVAULT_EMBED_MODEL", "bge-m3")


def _normalize(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def embed(texts, url=None, model=None, timeout=180):
    if isinstance(texts, str):
        texts = [texts]
    body = json.dumps({"input": texts, "model": model or DEFAULT_MODEL}).encode()
    req = urllib.request.Request(
        url or DEFAULT_URL, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        d = json.load(resp)
    items = sorted(d["data"], key=lambda x: x.get("index", 0))
    return [_normalize(x["embedding"]) for x in items]
