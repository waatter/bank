# TROPOS 3.2.3 美团官方跑腿连接器部署说明

## 交付边界

3.2.3 为 Linux Docker 部署补齐 Node 运行环境、官方程序只读挂载、组件级就绪检查和按 TROPOS 用户隔离的持久化授权目录。Mock 购物仍独立可用；真实询价、创建订单和支付跳转只有在用户本人完成美团授权后才开放。

美团官方仓库当前未提供许可证文件，因此官方 `paotui.js` 与 `pt-passport` 不进入 TROPOS 源码仓库或普通发布包。管理员使用 `scripts/package_meituan_connector.py` 从官方仓库生成独立私有部署包，并在重新分发前自行核对上游条款。

## 安全设计

- Node 在应用镜像中提供，官方程序通过只读卷挂载。
- 授权状态仅写入 `deploy/meituan/runtime/state`，目录所有者为容器用户 UID 10001，权限为 0700。
- 每位 TROPOS 用户使用用户 ID 的 SHA-256 目录，子进程 HOME、临时目录和 Token 缓存互相隔离。
- Token 不进入浏览器、SQLite、模型上下文、审计记录、普通部署包或连接器包。
- 真实订单仍须预览、明确确认；配送费超过 100 元需要额外确认；支付由用户在美团收银台完成。

## 构建来源

- 官方仓库：`https://github.com/meituan/MT-Paotui-For-Client`
- 本次核对提交：`05ead232ae2b94b9b55b2a1e126e039a1cb6db00`
- 授权工具：`pt-passport 1.0.5`
- Node 要求：18 或以上；Docker 镜像使用 Node 22 Bookworm 运行时。

## 服务器目录

将独立连接器包解压到 `/opt/tropos/deploy/meituan`，然后执行：

```bash
sudo sh /opt/tropos-candidate-3.2.3-20261031/deploy/prepare-meituan-connector.sh \
  /opt/tropos/deploy/meituan
```

普通升级只替换应用镜像，不删除 `runtime/state`。备份时应加密保存该目录；恢复时继续保持 UID 10001 和 0700 权限。
