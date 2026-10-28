# TROPOS v3 只读验收记录

UTC：2026-10-27T13:43:31.007545+00:00

只证明本次 GET 与模板观察；真人手机、业务和跨境稳定性仍待验收。

## public — https://demo.troposagent.com/

- 失败 · http /api/health：读取失败（SSLEOFError）；无正文或敏感头记录
- 失败 · http /：读取失败（SSLEOFError）；无正文或敏感头记录
- 失败 · http /demo.html：读取失败（SSLEOFError）；无正文或敏感头记录
- 失败 · http /study.html：读取失败（SSLEOFError）；无正文或敏感头记录
- 失败 · tls ：所有 HTTPS 请求均使用系统信任链及主机名验证；不跟随重定向
- 未测 · browser_runtime ：未执行 JavaScript；不建立会话，不调用其它 API，业务连接与渲染待人工验收
- 未测 · mobile_network ：无 VPN 的真人手机 Wi-Fi/蜂窝、韩国跨境链路稳定性待实测
- 未测 · origin_connection ：未提供控制台本机 --origin-url；公网 health 不能定位源站/代理问题

| GET 路径 | HTTP | 字节 | 首字节 ms | 完整响应 ms |
|---|---:|---:|---:|---:|
| /api/health | None | 0 | — | 5040.2 |
| / | None | 0 | — | 5010.6 |
| /demo.html | None | 0 | — | 5016.7 |
| /study.html | None | 0 | — | 5009.4 |

## 本地部署模板

- 已通过 · docker_version：Dockerfile 包含 VERSION 和页面
- 已通过 · docker_user_health：非 root 用户及回环 health 声明
- 已通过 · readonly_sqlite_tmpfs：模板：回环监听、只读根、/tmp tmpfs、独立 SQLite 可写目录；运行权限尚未验证
- 已通过 · caddy_proxy：模板：保持原有 Host/Origin 边界及健康依赖
- 已通过 · build_context：模板使用隔离 build-context 和默认拒绝的打包清单；上传前仍需实物检查
- 已通过 · context_sources：白名单源文件齐全且无符号链接；含运行必需的两份 backend JSON 与字体许可
- 未测 · staged_context：未生成 deploy/build-context；上传包及实际构建上下文未测
- 未测 · docker_runtime：未调用 Docker；镜像构建、Caddy 实际配置、SQLite 写权限/重启持久化须在控制台验收

汇总：已通过 6；失败 5；未测 5
