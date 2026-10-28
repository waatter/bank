# TROPOS v3 只读验收记录

UTC：2026-10-27T13:43:30.608230+00:00

只证明本次 GET 与模板观察；真人手机、业务和跨境稳定性仍待验收。

## public — http://127.0.0.1:8769/

- 已通过 · http /api/health：200；完整响应；GET，无凭据
- 已通过 · content_type /api/health：按资源类型核对 MIME
- 已通过 · cache /api/health：要求 Cache-Control: no-store 且 Age 缺省或为 0
- 已通过 · latency /api/health：单次完整响应 11.1 ms；阈值 3000 ms
- 已通过 · health_contract /api/health：ok / sandbox / simulated-persona-session
- 已通过 · version_3 /api/health：要求 3.x.x 版本
- 已通过 · model_mode /api/health：要求 modelMode=rules；仅健康声明，不是模型实测
- 已通过 · http /：200；完整响应；GET，无凭据
- 已通过 · content_type /：按资源类型核对 MIME
- 已通过 · cache /：要求 Cache-Control: no-store 且 Age 缺省或为 0
- 已通过 · latency /：单次完整响应 1.3 ms；阈值 3000 ms
- 已通过 · http /demo.html：200；完整响应；GET，无凭据
- 已通过 · content_type /demo.html：按资源类型核对 MIME
- 已通过 · cache /demo.html：要求 Cache-Control: no-store 且 Age 缺省或为 0
- 已通过 · latency /demo.html：单次完整响应 1.0 ms；阈值 3000 ms
- 已通过 · http /study.html：200；完整响应；GET，无凭据
- 已通过 · content_type /study.html：按资源类型核对 MIME
- 已通过 · cache /study.html：要求 Cache-Control: no-store 且 Age 缺省或为 0
- 已通过 · latency /study.html：单次完整响应 4.5 ms；阈值 3000 ms
- 已通过 · http /assets/tropos-mark.svg：200；完整响应；GET，无凭据
- 已通过 · content_type /assets/tropos-mark.svg：按资源类型核对 MIME
- 已通过 · cache /assets/tropos-mark.svg：要求 Cache-Control: no-store 且 Age 缺省或为 0
- 已通过 · latency /assets/tropos-mark.svg：单次完整响应 1.4 ms；阈值 3000 ms
- 已通过 · http /assets/home.css：200；完整响应；GET，无凭据
- 已通过 · content_type /assets/home.css：按资源类型核对 MIME
- 已通过 · cache /assets/home.css：要求 Cache-Control: no-store 且 Age 缺省或为 0
- 已通过 · latency /assets/home.css：单次完整响应 2.3 ms；阈值 3000 ms
- 已通过 · http /assets/typography.css：200；完整响应；GET，无凭据
- 已通过 · content_type /assets/typography.css：按资源类型核对 MIME
- 已通过 · cache /assets/typography.css：要求 Cache-Control: no-store 且 Age 缺省或为 0
- 已通过 · latency /assets/typography.css：单次完整响应 1.7 ms；阈值 3000 ms
- 已通过 · http /assets/home.js：200；完整响应；GET，无凭据
- 已通过 · content_type /assets/home.js：按资源类型核对 MIME
- 已通过 · cache /assets/home.js：要求 Cache-Control: no-store 且 Age 缺省或为 0
- 已通过 · latency /assets/home.js：单次完整响应 1.2 ms；阈值 3000 ms
- 已通过 · http /assets/demo.css：200；完整响应；GET，无凭据
- 已通过 · content_type /assets/demo.css：按资源类型核对 MIME
- 已通过 · cache /assets/demo.css：要求 Cache-Control: no-store 且 Age 缺省或为 0
- 已通过 · latency /assets/demo.css：单次完整响应 1.2 ms；阈值 3000 ms
- 已通过 · http /assets/profile-ui.css：200；完整响应；GET，无凭据
- 已通过 · content_type /assets/profile-ui.css：按资源类型核对 MIME
- 已通过 · cache /assets/profile-ui.css：要求 Cache-Control: no-store 且 Age 缺省或为 0
- 已通过 · latency /assets/profile-ui.css：单次完整响应 1.2 ms；阈值 3000 ms
- 已通过 · http /assets/life-ui.css：200；完整响应；GET，无凭据
- 已通过 · content_type /assets/life-ui.css：按资源类型核对 MIME
- 已通过 · cache /assets/life-ui.css：要求 Cache-Control: no-store 且 Age 缺省或为 0
- 已通过 · latency /assets/life-ui.css：单次完整响应 1.2 ms；阈值 3000 ms
- 已通过 · http /assets/action-ui.css：200；完整响应；GET，无凭据
- 已通过 · content_type /assets/action-ui.css：按资源类型核对 MIME
- 已通过 · cache /assets/action-ui.css：要求 Cache-Control: no-store 且 Age 缺省或为 0
- 已通过 · latency /assets/action-ui.css：单次完整响应 1.4 ms；阈值 3000 ms
- 已通过 · http /assets/shopping-ui.css：200；完整响应；GET，无凭据
- 已通过 · content_type /assets/shopping-ui.css：按资源类型核对 MIME
- 已通过 · cache /assets/shopping-ui.css：要求 Cache-Control: no-store 且 Age 缺省或为 0
- 已通过 · latency /assets/shopping-ui.css：单次完整响应 1.0 ms；阈值 3000 ms
- 已通过 · http /assets/personal-ui.css：200；完整响应；GET，无凭据
- 已通过 · content_type /assets/personal-ui.css：按资源类型核对 MIME
- 已通过 · cache /assets/personal-ui.css：要求 Cache-Control: no-store 且 Age 缺省或为 0
- 已通过 · latency /assets/personal-ui.css：单次完整响应 1.5 ms；阈值 3000 ms
- 已通过 · http /assets/chat-ui.css：200；完整响应；GET，无凭据
- 已通过 · content_type /assets/chat-ui.css：按资源类型核对 MIME
- 已通过 · cache /assets/chat-ui.css：要求 Cache-Control: no-store 且 Age 缺省或为 0
- 已通过 · latency /assets/chat-ui.css：单次完整响应 1.5 ms；阈值 3000 ms
- 已通过 · http /assets/connection-ui.css：200；完整响应；GET，无凭据
- 已通过 · content_type /assets/connection-ui.css：按资源类型核对 MIME
- 已通过 · cache /assets/connection-ui.css：要求 Cache-Control: no-store 且 Age 缺省或为 0
- 已通过 · latency /assets/connection-ui.css：单次完整响应 1.0 ms；阈值 3000 ms
- 已通过 · http /assets/profile-engine.js：200；完整响应；GET，无凭据
- 已通过 · content_type /assets/profile-engine.js：按资源类型核对 MIME
- 已通过 · cache /assets/profile-engine.js：要求 Cache-Control: no-store 且 Age 缺省或为 0
- 已通过 · latency /assets/profile-engine.js：单次完整响应 1.1 ms；阈值 3000 ms
- 已通过 · http /assets/profile-ui.js：200；完整响应；GET，无凭据
- 已通过 · content_type /assets/profile-ui.js：按资源类型核对 MIME
- 已通过 · cache /assets/profile-ui.js：要求 Cache-Control: no-store 且 Age 缺省或为 0
- 已通过 · latency /assets/profile-ui.js：单次完整响应 1.2 ms；阈值 3000 ms
- 已通过 · http /assets/backend-client.js：200；完整响应；GET，无凭据
- 已通过 · content_type /assets/backend-client.js：按资源类型核对 MIME
- 已通过 · cache /assets/backend-client.js：要求 Cache-Control: no-store 且 Age 缺省或为 0
- 已通过 · latency /assets/backend-client.js：单次完整响应 0.8 ms；阈值 3000 ms
- 已通过 · http /assets/life-ui.js：200；完整响应；GET，无凭据
- 已通过 · content_type /assets/life-ui.js：按资源类型核对 MIME
- 已通过 · cache /assets/life-ui.js：要求 Cache-Control: no-store 且 Age 缺省或为 0
- 已通过 · latency /assets/life-ui.js：单次完整响应 1.3 ms；阈值 3000 ms
- 已通过 · http /assets/action-ui.js：200；完整响应；GET，无凭据
- 已通过 · content_type /assets/action-ui.js：按资源类型核对 MIME
- 已通过 · cache /assets/action-ui.js：要求 Cache-Control: no-store 且 Age 缺省或为 0
- 已通过 · latency /assets/action-ui.js：单次完整响应 1.2 ms；阈值 3000 ms
- 已通过 · http /assets/shopping-ui.js：200；完整响应；GET，无凭据
- 已通过 · content_type /assets/shopping-ui.js：按资源类型核对 MIME
- 已通过 · cache /assets/shopping-ui.js：要求 Cache-Control: no-store 且 Age 缺省或为 0
- 已通过 · latency /assets/shopping-ui.js：单次完整响应 2.3 ms；阈值 3000 ms
- 已通过 · http /assets/personal-ui.js：200；完整响应；GET，无凭据
- 已通过 · content_type /assets/personal-ui.js：按资源类型核对 MIME
- 已通过 · cache /assets/personal-ui.js：要求 Cache-Control: no-store 且 Age 缺省或为 0
- 已通过 · latency /assets/personal-ui.js：单次完整响应 1.8 ms；阈值 3000 ms
- 已通过 · http /assets/chat-ui.js：200；完整响应；GET，无凭据
- 已通过 · content_type /assets/chat-ui.js：按资源类型核对 MIME
- 已通过 · cache /assets/chat-ui.js：要求 Cache-Control: no-store 且 Age 缺省或为 0
- 已通过 · latency /assets/chat-ui.js：单次完整响应 1.6 ms；阈值 3000 ms
- 已通过 · http /assets/connection-ui.js：200；完整响应；GET，无凭据
- 已通过 · content_type /assets/connection-ui.js：按资源类型核对 MIME
- 已通过 · cache /assets/connection-ui.js：要求 Cache-Control: no-store 且 Age 缺省或为 0
- 已通过 · latency /assets/connection-ui.js：单次完整响应 1.1 ms；阈值 3000 ms
- 已通过 · http /assets/demo.js：200；完整响应；GET，无凭据
- 已通过 · content_type /assets/demo.js：按资源类型核对 MIME
- 已通过 · cache /assets/demo.js：要求 Cache-Control: no-store 且 Age 缺省或为 0
- 已通过 · latency /assets/demo.js：单次完整响应 1.9 ms；阈值 3000 ms
- 已通过 · http /assets/study.css：200；完整响应；GET，无凭据
- 已通过 · content_type /assets/study.css：按资源类型核对 MIME
- 已通过 · cache /assets/study.css：要求 Cache-Control: no-store 且 Age 缺省或为 0
- 已通过 · latency /assets/study.css：单次完整响应 4.8 ms；阈值 3000 ms
- 已通过 · http /assets/study.js：200；完整响应；GET，无凭据
- 已通过 · content_type /assets/study.js：按资源类型核对 MIME
- 已通过 · cache /assets/study.js：要求 Cache-Control: no-store 且 Age 缺省或为 0
- 已通过 · latency /assets/study.js：单次完整响应 2.0 ms；阈值 3000 ms
- 已通过 · http /assets/fonts/noto-sans-sc-ui.woff2：200；完整响应；GET，无凭据
- 已通过 · content_type /assets/fonts/noto-sans-sc-ui.woff2：按资源类型核对 MIME
- 已通过 · cache /assets/fonts/noto-sans-sc-ui.woff2：要求 Cache-Control: no-store 且 Age 缺省或为 0
- 已通过 · latency /assets/fonts/noto-sans-sc-ui.woff2：单次完整响应 20.9 ms；阈值 3000 ms
- 已通过 · font_signature /assets/fonts/noto-sans-sc-ui.woff2：WOFF2 文件签名
- 已通过 · http /assets/fonts/manrope-latin.woff2：200；完整响应；GET，无凭据
- 已通过 · content_type /assets/fonts/manrope-latin.woff2：按资源类型核对 MIME
- 已通过 · cache /assets/fonts/manrope-latin.woff2：要求 Cache-Control: no-store 且 Age 缺省或为 0
- 已通过 · latency /assets/fonts/manrope-latin.woff2：单次完整响应 3.3 ms；阈值 3000 ms
- 已通过 · font_signature /assets/fonts/manrope-latin.woff2：WOFF2 文件签名
- 未测 · tls ：仅用于字面 127/8 回环 HTTP；未验证公网 HTTPS
- 未测 · browser_runtime ：未执行 JavaScript；不建立会话，不调用其它 API，业务连接与渲染待人工验收
- 未测 · mobile_network ：无 VPN 的真人手机 Wi-Fi/蜂窝、韩国跨境链路稳定性待实测
- 未测 · origin_connection ：未提供控制台本机 --origin-url；公网 health 不能定位源站/代理问题

| GET 路径 | HTTP | 字节 | 首字节 ms | 完整响应 ms |
|---|---:|---:|---:|---:|
| /api/health | 200 | 143 | 11.0 | 11.1 |
| / | 200 | 14910 | 1.2 | 1.3 |
| /demo.html | 200 | 17904 | 1.0 | 1.0 |
| /study.html | 200 | 5040 | 4.5 | 4.5 |
| /assets/tropos-mark.svg | 200 | 717 | 1.3 | 1.4 |
| /assets/home.css | 200 | 29752 | 2.0 | 2.3 |
| /assets/typography.css | 200 | 1523 | 1.7 | 1.7 |
| /assets/home.js | 200 | 11466 | 1.1 | 1.2 |
| /assets/demo.css | 200 | 40204 | 1.1 | 1.2 |
| /assets/profile-ui.css | 200 | 16687 | 1.1 | 1.2 |
| /assets/life-ui.css | 200 | 9292 | 1.1 | 1.2 |
| /assets/action-ui.css | 200 | 13045 | 1.3 | 1.4 |
| /assets/shopping-ui.css | 200 | 7224 | 1.0 | 1.0 |
| /assets/personal-ui.css | 200 | 4561 | 1.4 | 1.5 |
| /assets/chat-ui.css | 200 | 3453 | 1.4 | 1.5 |
| /assets/connection-ui.css | 200 | 1024 | 1.0 | 1.0 |
| /assets/profile-engine.js | 200 | 14865 | 1.1 | 1.1 |
| /assets/profile-ui.js | 200 | 34945 | 1.1 | 1.2 |
| /assets/backend-client.js | 200 | 13203 | 0.7 | 0.8 |
| /assets/life-ui.js | 200 | 34903 | 1.3 | 1.3 |
| /assets/action-ui.js | 200 | 28746 | 1.1 | 1.2 |
| /assets/shopping-ui.js | 200 | 18974 | 2.3 | 2.3 |
| /assets/personal-ui.js | 200 | 7878 | 1.6 | 1.8 |
| /assets/chat-ui.js | 200 | 16155 | 1.5 | 1.6 |
| /assets/connection-ui.js | 200 | 2904 | 1.0 | 1.1 |
| /assets/demo.js | 200 | 156937 | 1.4 | 1.9 |
| /assets/study.css | 200 | 3591 | 4.7 | 4.8 |
| /assets/study.js | 200 | 15537 | 2.0 | 2.0 |
| /assets/fonts/noto-sans-sc-ui.woff2 | 200 | 1029776 | 20.1 | 20.9 |
| /assets/fonts/manrope-latin.woff2 | 200 | 28712 | 3.2 | 3.3 |

## 本地部署模板

- 已通过 · docker_version：Dockerfile 包含 VERSION 和页面
- 已通过 · docker_user_health：非 root 用户及回环 health 声明
- 已通过 · readonly_sqlite_tmpfs：模板：回环监听、只读根、/tmp tmpfs、独立 SQLite 可写目录；运行权限尚未验证
- 已通过 · caddy_proxy：模板：保持原有 Host/Origin 边界及健康依赖
- 已通过 · build_context：模板使用隔离 build-context 和默认拒绝的打包清单；上传前仍需实物检查
- 已通过 · context_sources：白名单源文件齐全且无符号链接；含运行必需的两份 backend JSON 与字体许可
- 未测 · staged_context：未生成 deploy/build-context；上传包及实际构建上下文未测
- 未测 · docker_runtime：未调用 Docker；镜像构建、Caddy 实际配置、SQLite 写权限/重启持久化须在控制台验收

汇总：已通过 131；失败 0；未测 6
