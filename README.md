# MarkStackAI News Intelligence

**A self-hosted, real-time RSS news aggregation dashboard with smart filtering and live updates.**

**一个自托管的实时 RSS 新闻聚合面板，支持智能过滤和实时更新。**

> If you find this project helpful, please consider giving it a **Star** — it means a lot and keeps us motivated!
>
> 如果这个项目对你有帮助，请给个 **Star** 支持一下，这对我们非常重要！

---

## Preview / 预览

🌐 **Live Demo / 在线演示**: [http://74.48.24.131:37378](http://74.48.24.131:37378)

---

## Features / 功能特性

| Feature | 功能 |
|---|---|
| 53 built-in RSS sources covering politics, economics, crypto, tech, defense & more | 内置 53 个 RSS 源，覆盖政治、经济、加密货币、科技、军事等领域 |
| Web-based feed management — add, delete, enable/disable feeds without restarting | 网页端源管理 — 增删、启用/禁用 RSS 源，无需重启 |
| Multi-dimensional filtering: Category / Region / Source | 多维度过滤：分类 / 地区 / 来源 |
| Real-time updates via WebSocket | WebSocket 实时推送更新 |
| Full-text search with URL state persistence | 全文搜索，URL 状态持久化 |
| Dark / Light theme auto-detection | 深色 / 浅色主题自动切换 |
| Bilingual UI (English / Chinese) | 双语界面（中文 / 英文） |
| Keyboard shortcuts (j/k/o/r/?) | 键盘快捷键 (j/k/o/r/?) |
| Mobile responsive with hamburger sidebar | 移动端适配，汉堡菜单侧栏 |
| SQLite persistence with WAL mode | SQLite 持久化存储，WAL 模式 |
| One-click deployment script with Supervisor | 一键部署脚本，Supervisor 后台守护 |
| Docker support with multi-stage build | Docker 支持，多阶段构建 |
| Security headers, GZip compression, connection limits | 安全头、GZip 压缩、连接数限制 |
| Conditional HTTP requests (ETag / If-Modified-Since) | HTTP 条件请求（ETag / If-Modified-Since） |

---

## Quick Start / 快速开始

### Requirements / 环境要求

- Python 3.9+

### Option 1: One-Click Deploy (Recommended) / 一键部署（推荐）

```bash
git clone https://github.com/zerotoonex/markstackai-news.git
cd markstackai-news
sudo bash deploy.sh install
```

This will automatically:
- Install Python3, pip, Supervisor (if not present)
- Create a virtual environment and install dependencies
- Configure Supervisor for background running with auto-restart
- Start the service on port **37378**

以上命令会自动完成：
- 安装 Python3、pip、Supervisor（如未安装）
- 创建虚拟环境并安装依赖
- 配置 Supervisor 后台守护进程（崩溃自动重启）
- 在端口 **37378** 启动服务

Visit / 访问：`http://your-server-ip:37378`

### Option 2: Manual Run / 手动运行

```bash
git clone https://github.com/zerotoonex/markstackai-news.git
cd markstackai-news
pip install -r requirements.txt
python rss_viewer.py
```

### Option 3: Docker

```bash
docker build -t markstackai-news .
docker run -d -p 37378:37378 -v news-data:/app/data markstackai-news
```

---

## Management Commands / 管理命令

```bash
sudo bash deploy.sh status     # View status / 查看状态
sudo bash deploy.sh restart    # Restart service / 重启服务
sudo bash deploy.sh logs       # View logs / 查看日志
sudo bash deploy.sh follow     # Follow logs in real-time / 实时日志
sudo bash deploy.sh stop       # Stop service / 停止服务
sudo bash deploy.sh update     # Update dependencies & restart / 更新依赖并重启
sudo bash deploy.sh uninstall  # Uninstall (keeps data) / 卸载（保留数据）
sudo bash deploy.sh            # Interactive menu / 交互式菜单
```

---

## Feed Management / 数据源管理

Click the **gear icon** (⚙) in the top-right corner to manage RSS feeds:

点击右上角 **齿轮图标**（⚙）管理 RSS 数据源：

- **Add** — Enter RSS URL and category, click `+ Add`
- **Search** — Filter existing feeds by URL or category
- **Enable/Disable** — Toggle feeds on/off without deleting
- **Delete** — Remove feeds permanently

Changes take effect on the next refresh cycle (every 5 minutes).

修改在下一个刷新周期（每 5 分钟）自动生效。

---

## Tech Stack / 技术栈

| Component | Technology |
|---|---|
| Backend | Python, Starlette (ASGI), Uvicorn |
| Database | SQLite (WAL mode) |
| RSS Parsing | feedparser |
| Frontend | Vanilla HTML/CSS/JS (single-file, no build step) |
| Real-time | WebSocket |
| Process Manager | Supervisor |
| Container | Docker (multi-stage build) |

---

## Project Structure / 项目结构

```
markstackai-news/
├── rss_viewer.py      # Main application (backend + frontend)
│                      # 主应用（后端 + 前端）
├── requirements.txt   # Python dependencies / Python 依赖
├── deploy.sh          # One-click deployment script / 一键部署脚本
├── Dockerfile         # Docker build file / Docker 构建文件
└── README.md          # This file / 本文件
```

---

## Configuration / 配置

| Parameter | Default | Description |
|---|---|---|
| `--port` | 37378 | Server port / 服务端口 |
| `--host` | 0.0.0.0 | Bind address / 绑定地址 |
| `--db` | data/rss_news.db | Database path / 数据库路径 |

Example / 示例:

```bash
python rss_viewer.py --port 8080 --db /var/data/news.db
```

---

## Supported Platforms / 支持平台

| Platform | Deploy Script | Docker |
|---|---|---|
| Ubuntu / Debian | ✅ | ✅ |
| CentOS / RHEL / Rocky | ✅ | ✅ |
| Alpine Linux | ✅ | ✅ |
| macOS | ✅ (nohup mode) | ✅ |

---

## Credits / 致谢

This project is partially inspired by and sources some RSS feed configurations from:

本项目部分灵感和 RSS 数据源配置来自：

- [situation-monitor](https://github.com/hipcityreg/situation-monitor) by [@hipcityreg](https://github.com/hipcityreg)

---

## License / 许可证

MIT License

---

<p align="center">
  <b>Powered by <a href="https://markstackai.com">MarkStackAI</a></b>
</p>
