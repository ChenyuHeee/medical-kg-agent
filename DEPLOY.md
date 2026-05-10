# 部署指南

本项目分两块：**前端 SPA**（`src/web/index.html`，纯静态）+ **FastAPI 后端**（`src/api/app.py` + `data/` + `textbooks/`）。

---

## 一、后端部署到 ModelScope 创空间（推荐）

ModelScope 创空间支持自定义 Docker 镜像，免费、永远在线、国内访问快。

### 1. 在魔搭新建创空间
1. 打开 <https://www.modelscope.cn/studios/create>
2. 选择 **「Custom Build」** 或 **「自定义 Docker」**
3. SDK 选 `docker`，端口填 `8000`
4. 创建后会得到一个 git 仓库地址，例如 `https://www.modelscope.cn/studios/<你的用户名>/medical-kg.git`

### 2. 推送代码到魔搭仓库
```bash
git remote add ms https://www.modelscope.cn/studios/<你的用户名>/medical-kg.git
git lfs install
# textbooks/ 太大，要走 LFS
git lfs track "textbooks/*.pdf"
git add .gitattributes Dockerfile requirements.txt src/ data/ textbooks/
git commit -m "deploy: initial"
git push ms main
```

### 3. 配置环境变量（魔搭 Studio 设置页）
| 变量 | 值 |
|---|---|
| `MODELSCOPE_API_KEY` | 你的 DeepSeek/魔搭 key |
| `MODELSCOPE_BASE_URL` | `https://api.deepseek.com/v1` |
| `MODELSCOPE_MODEL` | `deepseek-chat` |

### 4. 部署完成后会得到公网域名
形如 `https://<用户名>-medical-kg.modelscope.cn`，记下来给前端用。

---

## 二、前端部署到 GitHub Pages

已配好 `.github/workflows/pages.yml`，自动部署。

### 1. 在仓库 Settings 启用 Pages
- Settings → Pages → Source = **GitHub Actions**

### 2. 在 Repository Secrets 填后端地址
- Settings → Secrets and variables → Actions → New repository secret
- Name: `API_BASE`
- Value: `https://<用户名>-medical-kg.modelscope.cn`

### 3. 推送任意改动到 `src/web/` 触发部署
```bash
git push
```
完成后访问 `https://chenyuheee.github.io/medical-kg-agent/`。

### 用户也可在 URL 临时切换后端
```
https://chenyuheee.github.io/medical-kg-agent/?api=https://your-other-backend.com
```
该值会写入 localStorage，下次访问自动使用。

---

## 三、本地开发

```bash
# 后端
uvicorn src.api.app:app --reload --port 8000
# 前端：直接打开 http://localhost:8000/  （后端会托管 src/web/）
```

---

## 四、备选后端平台

| 平台 | 成本 | 是否休眠 | 备注 |
|---|---|---|---|
| ModelScope Studio | 免费 | 不休眠（有访问） | 国内首选 |
| Hugging Face Spaces | 免费 | 48h 无访问休眠 | 海外 |
| Fly.io | 免费额度 | 不休眠 | 需信用卡 |
| Render | 免费 | 15min 休眠 | 简单 |

任意 Docker 平台都可直接用本仓库的 `Dockerfile`。
