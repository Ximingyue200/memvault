"""用 bge-m3 embedding 做零样本分类（零成本、本地、可全量）。"""
import json
import os
import sqlite3
import urllib.request

import numpy as np

EMB = os.environ.get("MEMVAULT_EMBED_URL", "http://127.0.0.1:8081")
MODEL = os.environ.get("MEMVAULT_EMBED_MODEL", "bge-m3")

# 每个类配一组「代表说法」，比单句描述更稳
DESC = {
    "硬件/CPU": "CPU 处理器 AMD XXXX 7840 9850X3D Intel 锐龙 霄龙 核心 线程 主频",
    "硬件/内存": "内存 DDR4 DDR5 内存条 频率 时序 ECC 容量",
    "硬件/存储": "硬盘 SSD NVMe 傲腾 固态 SATA 机械盘 扩容盘 分区 RAID 文件系统",
    "硬件/主板整机": "主板 整机 迷你主机 机箱 电源 BIOS 固件 USB4 雷电 准系统",
    "硬件/网络设备": "路由器 交换机 光猫 网卡 无线 AP OpenWrt 软路由 随身WiFi 网口 万兆",
    "硬件/显示外设": "显示器 HDMI 屏幕 分辨率 刷新率 键盘 鼠标 显卡 独显 集显 输出",
    "硬件/手机平板": "手机 平板 小米 魅族 红米 平板电脑 屏幕 电池 充电",
    "系统/虚拟化": "PVE Proxmox 虚拟机 LXC 容器 直通 快照 备份 集群 ESXi Docker",
    "系统/网络": "网络 DNS DHCP 路由 防火墙 代理 VPN 内网穿透 frp NAT 端口 带宽 测速",
    "系统/Linux": "Linux Debian Ubuntu systemd 服务 进程 权限 shell bash 内核 日志",
    "系统/Windows": "Windows WSL 驱动 注册表 组策略 蓝屏 系统盘",
    "开发/语言": "Python Java JavaScript TypeScript Go Rust 语法 库 函数 编译",
    "开发/工具": "Git Docker 命令行 CLI 脚本 编辑器 调试 构建 配置 文件格式 YAML JSON",
    "开发/AI模型": "大模型 LLM 推理 训练 embedding 向量 提示词 智能体 agent RAG 微调",
    "应用/记忆库": "记忆库 记忆 卡片 提炼 检索 召回 编年史 梦境 OpenViking memvault cradle",
    "应用/游戏": "游戏 MOD 模组 引擎 存档 联机 红警 我的世界 Minecraft BepInEx",
    "应用/手机": "安卓 Android 刷机 Root Magisk 模块 解锁 Bootloader IMEI 面具 面具模块",
    "应用/网络服务": "网盘 云盘 CDN 对象存储 图床 域名 证书 备案 服务器 VPS 主机租用",
    "应用/硬件设计": "电路 原理图 PCB 焊接 单片机 ESP32 STM32 PLC CAD 3D打印 建模",
    "其它": "其它 杂项 生活 日常 无关",
}

BASE = os.environ.get("MEMVAULT_EMBED_URL", "http://127.0.0.1:8081")


def embed(texts, batch=16):
    out = []
    for i in range(0, len(texts), batch):
        chunk = texts[i:i + batch]
        body = json.dumps({"input": chunk, "model": MODEL}).encode()
        req = urllib.request.Request(
            BASE.rstrip("/") + "/v1/embeddings", data=body,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as r:
            d = json.loads(r.read())
        for it in d["data"]:
            v = np.array(it["embedding"], dtype=np.float32)
            n = np.linalg.norm(v) or 1.0
            out.append(v / n)
    return np.vstack(out)


def main(db):
    con = sqlite3.connect(db)
    rows = list(con.execute(
        "SELECT id, COALESCE(NULLIF(title,''), substr(body,1,60)) t FROM cards "
        "WHERE status='active'"))
    print(f"cards: {len(rows)}", flush=True)

    names = list(DESC.keys())
    C = embed([DESC[n] for n in names])
    print(f"class vecs: {C.shape}", flush=True)

    ids = [r[0] for r in rows]
    texts = [r[1] or "" for r in rows]
    V = embed(texts)
    print(f"card vecs: {V.shape}", flush=True)

    S = V @ C.T                      # (N, 20)
    idx = S.argmax(axis=1)
    conf = S[np.arange(len(idx)), idx]

    upd = [(names[i], ids[k]) for k, i in enumerate(idx)]
    con.executemany("UPDATE cards SET category=? WHERE id=?", upd)
    con.commit()

    print("--- 分布 ---", flush=True)
    for r in con.execute(
            "SELECT category, count(*) n FROM cards WHERE status='active' "
            "GROUP BY category ORDER BY n DESC"):
        print(f"  {r[0]:16s} {r[1]:6d}")
    print(f"--- 置信度 ---\n  mean {conf.mean():.4f} "
          f"p10 {np.percentile(conf,10):.4f} "
          f"p50 {np.percentile(conf,50):.4f} "
          f"low(<0.55) {(conf<0.55).sum()}", flush=True)
    con.close()


if __name__ == "__main__":
    import sys
    main(sys.argv[1] if len(sys.argv) > 1 else "/opt/memvault/data/memvault.db")
