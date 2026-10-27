# TROPOS 公共轻量服务器部署

## 选型

- 阿里云轻量应用服务器，韩国（首尔）地域；腾讯云 Lighthouse 或普通 Linux 云服务器也可使用
- Linux 64 位 Docker 应用镜像
- 起步规格：2 vCPU、2 GB 内存、40 GB SSD 即可用于单用户比赛演示
- 安全组只开放 TCP 22（仅管理员 IP）、80、443

韩国节点用于减少大陆备案对当前演示的阻塞。正式面向公众经营或处理真实金融数据前，仍需重新评估备案、隐私、等保、数据跨境和银行合规要求。

## 当前部署边界

这是“单用户私有演示站”，不是多租户生产银行：

- 有域名时 Caddy 自动提供 HTTPS；没有域名时先通过公网 IP 使用临时 HTTP 地址。电脑和手机均免口令访问。
- TROPOS 应用只监听服务器的 `127.0.0.1:8765`，公网无法绕过 Caddy 直接访问。
- SQLite 保存在 `deploy/data/`，适合单实例演示；不要把它当作多人生产数据库。
- 公网演示没有身份认证。任何拿到网址的人都能查看并修改共享的合成数据，因此不得录入真实姓名、账号、地址、手机号或资金信息。
- 云端镜像不包含本机美团 Skill、个人美团 Token、模型密钥或 `.local` 数据。
- 美团官方跑腿在云端默认显示为未配置，只保留 Mock 购物。

## 部署步骤

1. 在云平台防火墙中开放 TCP 80、443；TCP 22 只允许管理员当前公网 IP。
2. 将项目上传到服务器，例如 `/opt/tropos`。
3. 在项目目录运行：

   ```bash
   chmod +x deploy/prepare.sh
   ./deploy/prepare.sh
   cd deploy
   docker compose --env-file .env.production up -d --build
   ```

   脚本会询问公网 IPv4 或域名。输入 IPv4 时会生成临时 `http://公网IP` 地址；输入域名时 Caddy 会自动配置 HTTPS。

4. 检查：

   ```bash
   docker compose ps
   docker compose logs --tail=100
   curl -I http://你的公网IP/demo.html
   ```

取得域名后，将 A 记录指向服务器公网 IPv4，再重新运行 `prepare.sh` 并输入域名，执行 `docker compose up -d`，即可从临时 HTTP 切换为自动 HTTPS。

## 数据与备份

停止服务后备份 `deploy/data/tropos.sqlite3`；恢复时把数据库放回相同目录并确保 UID 10001 可写。不要把 `.env.production`、数据库或备份提交到 Git。
