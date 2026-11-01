# 现有韩国实例部署入口

本轮只准备 `demo.troposagent.com` 现有实例的可验收更新，不购买服务器、不更改 DNS、不新增公网端口。应用保持 `127.0.0.1:8765`，Caddy 沿用现有域名及 HTTPS 配置，SQLite 使用独立持久化目录。

完整操作顺序、控制台只读诊断、根源码构建上下文准备、手机网络矩阵和回退见 [3.0 手机与部署验收](../docs/3.0_手机与部署验收.md)。

Compose 已改用 `deploy/build-context`，**不能在根源码解压后直接 `up --build`**。先按照验收文档中的 Python 白名单复制命令准备此目录，再运行构建。清单包括 `VERSION`、`index.html`、`demo.html`、**`study.html`**、后端 Python 和运行必需 JSON、前端静态文件/字体及许可、两份服务实际读取的报告。拒绝符号链接，不复制环境文件、账本、备份、发布包或 `.local`。

`build-context.dockerignore` 是隔离目录的默认拒绝模板；根目录旧 `.dockerignore` 并不足以保证构建上下文安全。`env.production.example` 只提供现有域名的非秘密示例。现有实例使用自己的 `.env.production`，不要覆盖或打印。旧 `prepare.sh` 面向首次部署，会重写配置和询问地址，本轮更新不用它。

本地只读检查器使用 Python 3.10+ 标准库：

```bash
python3 scripts/v3_readiness.py https://demo.troposagent.com \
  --output deploy/readiness-20261027-run01
python3 -m unittest discover -s tests -p test_v3_readiness.py -v
```

输出目录必须尚不存在。退出码 0 表示已执行检查无失败，仍可能含未测；1 表示失败；2 表示输入或输出问题。不能以退出码 0 宣称手机稳定或完整业务验收通过。

服务现在支持持久化用户名/密码账户、邮箱验证、邮件找回密码和 TOTP MFA，同时保留共享 A/B/C 演示入口。它仍不是银行实名系统：尚未接入证件、活体或银行卡要素核验，任何人均不应输入真实金融资料。生产部署必须配置 HTTPS、事务邮件 SMTP、持久化 `TROPOS_AUTH_MASTER_KEY`，并备份数据库旁的身份主密钥；韩国跨境网络仍需无 VPN 真人手机 Wi-Fi/蜂窝实测。
