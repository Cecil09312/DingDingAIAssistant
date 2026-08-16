# Docker 部署启动指南

本指南介绍如何在 Windows 与 Linux 系统上启动钉钉AI智能体助手的 Docker 容器。

项目已内置三层国内镜像加速：
- Docker 基础镜像：DaoCloud (`docker.m.daocloud.io`)
- apt (Debian) 源：阿里云 (`mirrors.aliyun.com`)
- pip 源：清华 (`pypi.tuna.tsinghua.edu.cn`)
- HuggingFace 模型源：`hf-mirror.com`

---

## 一、前置条件（项目无关）

### 1.1 必备文件清单

启动前请确认项目根目录下存在以下文件：

| 文件 | 用途 | 状态 |
|------|------|------|
| `Dockerfile` | 镜像构建脚本 | 已存在 |
| `docker-compose.yml` | 编排配置 | 已存在 |
| `.dockerignore` | 构建排除规则 | 已存在 |
| `requirements.txt` | Python 依赖清单 | 已存在 |
| `.env` | 运行时环境变量（密钥等） | 需自行配置 |
| `scripts/prefetch_models.py` | 构建期模型预下载脚本 | 已存在 |

### 1.2 .env 必填项

`.env` 文件至少需要包含以下配置项（参考 `.env.example`）：

```bash
# 大语言模型
LLM_API_KEY=your_api_key
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen-plus

# 钉钉
DINGTALK_APP_KEY=your_app_key
DINGTALK_APP_SECRET=your_app_secret

# 钉钉工具调用（待办/会议管理，默认关闭）
TOOL_CALLING_ENABLED=false
TOOL_CONFIRMATION_REQUIRED=true

# 其他配置项按 .env.example 补全
```

### 1.3 项目路径要求

**强烈建议**将项目放置在纯英文路径下，例如：

- Windows: `C:\projects\dingtalk-agent`
- Linux: `/opt/dingtalk-agent`

避免中文路径（如 `C:\直播课\...`）导致构建时编码异常。

---

## 二、Windows 系统启动步骤

### 2.1 安装 Docker Desktop

1. 下载地址：https://www.docker.com/products/docker-desktop/
2. 系统要求：
   - Windows 10 64 位 版本 2004 及以上，或 Windows 11
   - BIOS 开启虚拟化（VT-x / AMD-V）
   - 至少 4GB 内存（推荐 8GB+）
3. 安装时勾选 **"Use WSL 2 instead of Hyper-V"**（推荐方案）
4. 安装完成后**重启电脑**

### 2.2 启动 Docker Desktop

1. 打开 Docker Desktop 应用
2. 等待右下角鲸鱼图标停止动画
3. 确认左下角显示 **"Engine running"**

### 2.3 验证 docker 命令

**新开一个 PowerShell 窗口**（让 PATH 生效），执行：

```powershell
docker --version
docker compose version
```

能正常输出版本号即表示安装成功。

### 2.4 构建并启动容器

在项目根目录下执行：

```powershell
# 切换到项目目录
cd C:\projects\dingtalk-agent

# 构建镜像（首次较慢，需下载 torch 与模型，约 10-30 分钟）
docker compose build

# 后台启动容器
docker compose up -d

# 查看实时日志
docker compose logs -f agent

# 健康检查
curl http://localhost:8000/health
```

### 2.5 限制 WSL 2 内存（可选但推荐）

WSL 2 默认无内存上限，可能占用过多宿主机资源。在用户目录下创建文件：

`%USERPROFILE%\.wslconfig`

内容示例：

```ini
[wsl2]
memory=8GB
processors=4
swap=2GB
```

保存后重启 Docker Desktop 生效。

### 2.6 Windows 特有注意事项

| 事项 | 说明 |
|------|------|
| 路径包含中文 | 必须避免，否则构建时易出编码异常 |
| 行尾符 | Git 拉取时建议保留 LF（`git config --global core.autocrlf false`） |
| 端口占用 | 8000 端口需未被其他程序占用，可用 `netstat -ano \| findstr :8000` 检查 |
| 防火墙 | 首次启动 Windows 会弹出防火墙提示，需允许 Docker 通过 |
| 容器访问宿主机服务 | 通过 `host.docker.internal` 主机名访问，已在 compose 中配置 |

---

## 三、Linux 系统启动步骤

### 3.1 安装 Docker Engine

以 Ubuntu/Debian 为例（其他发行版参考 Docker 官方文档）：

```bash
# 卸载旧版（如有）
sudo apt-get remove docker docker-engine docker.io containerd runc

# 安装依赖
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg lsb-release

# 添加 Docker 官方 GPG key（使用清华镜像）
sudo mkdir -p /etc/apt/keyrings
curl -fsSL https://mirrors.tuna.tsinghua.edu.cn/docker-ce/linux/ubuntu/gpg | \
  sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg

# 添加 apt 源
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://mirrors.tuna.tsinghua.edu.cn/docker-ce/linux/ubuntu $(lsb_release -cs) stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# 安装
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# 将当前用户加入 docker 组（免 sudo）
sudo usermod -aG docker $USER

# 重新登录或执行以下命令使组生效
newgrp docker
```

### 3.2 配置 Docker 国内镜像加速（可选）

创建或编辑 `/etc/docker/daemon.json`：

```json
{
  "registry-mirrors": [
    "https://docker.m.daocloud.io",
    "https://dockerproxy.com",
    "https://docker.nju.edu.cn"
  ]
}
```

重启 Docker 服务：

```bash
sudo systemctl daemon-reload
sudo systemctl restart docker
```

### 3.3 验证 docker 命令

```bash
docker --version
docker compose version
```

### 3.4 构建并启动容器

```bash
# 切换到项目目录
cd /opt/dingtalk-agent

# 构建镜像
docker compose build

# 后台启动
docker compose up -d

# 查看日志
docker compose logs -f agent

# 健康检查
curl http://localhost:8000/health
```

### 3.5 Linux 特有注意事项

| 事项 | 说明 |
|------|------|
| 权限问题 | `data/` 目录需对容器内进程可写：`chmod -R 755 data/` |
| SELinux | 如启用了 SELinux，卷映射需加 `:z` 后缀：`./data:/app/data:z` |
| 防火墙 | 放行 8000 端口：`sudo ufw allow 8000` |
| 守护进程 | 确保开机自启：`sudo systemctl enable docker` |
| 容器访问宿主机服务 | 通过 `host.docker.internal` 访问，已在 compose 中配置 `host-gateway` |
| 资源限制 | 可在 `/etc/docker/daemon.json` 中配置 `default-memory` 等 |

---

## 四、通用运维命令

以下命令在 Windows 与 Linux 上完全一致：

```bash
# 查看运行状态
docker compose ps

# 查看实时日志
docker compose logs -f agent

# 停止容器
docker compose down

# 重启容器
docker compose restart

# 重新构建（代码或依赖变更后）
docker compose build --no-cache

# 升级（拉新代码后）
docker compose up -d --build

# 进入容器调试
docker compose exec agent bash

# 查看容器资源占用
docker stats
```

---

## 五、Docker 镜像复用

`docker compose build` 生成的镜像是一个本地静态文件，构建成功后可以反复使用，无需每次都重新构建。

### 5.1 同一台机器复用

```bash
docker compose up -d      # 直接用已构建的镜像启动，不会重新 build
docker compose down       # 停止并删除容器（镜像仍在）
docker compose up -d      # 再次启动，秒级（镜像已缓存）
```

### 5.2 什么时候会触发重新 build

| 场景 | 是否需要重新 build |
|------|--------------------|
| 修改 `.env`（密钥、配置） | 不需要（运行时通过 `env_file` 注入） |
| 修改 `data/` 里的数据 | 不需要（通过 `volumes` 挂载） |
| 修改项目源码或 `requirements.txt` | 需要（`docker compose build` 或 `docker compose up -d --build`） |
| 修改 `agent/tools/` 工具模块 | 需要（源码变更） |
| 修改 `.qoder/skills/` 钉钉 Skill | 需要（dingtalk_lib.py 被工具调用依赖） |
| 修改 `Dockerfile` | 需要 |
| 仅执行 `docker compose up -d` | 不会自动重新 build |

### 5.3 跨机器复用

**方式一：导出为文件（无需镜像仓库）**

```bash
# 在源机器上导出
docker save 钉钉ai智能体助手-agent -o agent.tar

# 拷贝 agent.tar 到目标机器后导入
docker load -i agent.tar
```

**方式二：推送到镜像仓库**

```bash
# 打标签并推送
docker tag 钉钉ai智能体助手-agent your-registry.com/agent:latest
docker push your-registry.com/agent:latest

# 目标机器拉取
docker pull your-registry.com/agent:latest
```

目标机器拿到镜像后，只需 `docker-compose.yml` + `.env` + `data/` 目录即可运行，不需要源码和 Dockerfile。

### 5.4 注意事项

- 镜像约 4-6 GB（含全部预下载模型：BGE Embedding + Reranker + OCR），导出/传输需要足够磁盘空间
- 镜像内烘焙的是构建时的模型版本，如果模型有更新需重新 build
- Windows 构建的镜像可以直接在 Linux 上运行（Docker 镜像跨平台兼容，前提是都是 CPU 版，没有绑 CUDA）
- `.qoder/skills/` 目录会被打入镜像（工具调用依赖 dingtalk_lib.py），`.qoder/repowiki/` 被排除

---

## 六、常见问题排查

### 6.1 构建阶段

#### Q1: 构建报错 `failed to compute cache key: "/requirements.txt" not found`

**原因**：当前目录不在项目根。

**解决**：确认在 `Dockerfile` 所在目录执行 `docker compose build`。

#### Q2: `pip install torch` 卡住或超时

**原因**：清华镜像偶发慢，或网络问题。

**解决**：
- 重试几次
- 或临时切换源：`pip install torch -i https://mirrors.aliyun.com/pypi/simple/`

#### Q3: `prefetch_models.py` 下载模型失败

**原因**：`hf-mirror.com` 偶发不可用。

**解决**：
- 重试构建
- 或先在本机预下载好模型，挂载进容器：
  ```yaml
  volumes:
    - ./data:/app/data
    - ./hf_cache:/app/.hf_cache
  ```

#### Q4: Windows 上路径含中文报错

**原因**：Docker Desktop 在某些版本下对中文路径处理异常。

**解决**：将项目移到纯英文路径，如 `C:\projects\dingtalk-agent`。

### 5.2 运行阶段

#### Q1: 容器启动后立即退出

**排查步骤**：

```bash
# 查看退出日志
docker compose logs agent

# 检查 .env 是否齐全
docker compose exec agent env | grep LLM_

# 检查端口占用
docker compose ps -a
```

#### Q2: 健康检查不通过

**原因**：服务启动慢（模型加载耗时）或异常。

**排查**：
```bash
# 查看应用日志
docker compose logs --tail=200 agent

# 进入容器手动测试
docker compose exec agent curl http://localhost:8000/health
```

`docker-compose.yml` 已设置 `start_period: 60s`，若仍超时，可适当调大。

#### Q3: 容器无法访问宿主机上的服务

**原因**：宿主机服务绑定到 `127.0.0.1` 而非 `0.0.0.0`。

**解决**：
- 将宿主机服务绑定到 `0.0.0.0`
- 或在容器内使用 `host.docker.internal:端口` 访问

#### Q4: 数据卷映射后容器内文件权限不足（Linux）

**原因**：宿主机文件属主与容器内进程用户不一致。

**解决**：
```bash
# 查看容器内进程用户
docker compose exec agent id

# 调整宿主机 data 目录权限
sudo chown -R 1000:1000 data/
# 或宽松权限
sudo chmod -R 777 data/
```

#### Q5: 工具调用功能（待办/会议）无法使用

**原因**：`TOOL_CALLING_ENABLED=false` 或钉钉应用权限不足。

**解决**：
1. 确认 `.env` 中 `TOOL_CALLING_ENABLED=true`
2. 确认钉钉开放平台应用已申请「待办」和「日历」权限
3. 确认 `DINGTALK_APP_KEY` 和 `DINGTALK_APP_SECRET` 配置正确
4. 查看日志确认路由是否正确识别为 `tool`：
   ```bash
   docker compose logs agent | grep "route"
   ```

#### Q6: 容器启动时 HuggingFace 模型下载超时

**原因**：构建期 `hf-mirror.com` 偶发不可用。

**解决**：
- 重试构建（`docker compose build --no-cache`）
- 或先在本机预下载模型，挂载进容器：
  ```yaml
  volumes:
    - ./data:/app/data
    - ./hf_cache:/app/.hf_cache
  ```
- 运行时已设置 `HF_HUB_OFFLINE=1`，不会尝试联网下载

---

## 六、生产环境建议

| 项 | 建议 |
|----|------|
| 反向代理 | 在容器外用 Nginx 反代 8000 端口，配置 HTTPS |
| 数据备份 | 定期备份 `./data` 目录（SQLite + 向量库） |
| 资源限制 | 在 compose 中加 `deploy.resources.limits` 限制 CPU/内存 |
| 日志收集 | 配置 `logging` driver，避免日志膨胀 |
| 镜像版本 | 生产环境固定 Dockerfile 中的基础镜像 tag，避免 `latest` |
| 健康检查 | 已配置，配合编排系统的就绪探针使用 |
| 重启策略 | 已配置 `restart: unless-stopped`，宿主机重启后自动恢复 |

---

## 八、参考文档

- Docker 官方文档：https://docs.docker.com/
- Docker Compose 文档：https://docs.docker.com/compose/
- Docker Desktop for Windows：https://docs.docker.com/desktop/install/windows-install/
- DaoCloud 镜像站：https://docker.m.daocloud.io/
- 阿里云 Debian 镜像：https://developer.aliyun.com/mirror/debian
- 清华 PyPI 镜像：https://mirrors.tuna.tsinghua.edu.cn/help/pypi/
- HF Mirror：https://hf-mirror.com/
