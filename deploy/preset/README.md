# 3X-UI 3.10.2 IPv6 + HY2 默认节点版

基于 3X-UI v3.10.2，Xray 内核继续固定为 **v26.6.27**。独立分支 `codex/v3.10.2-preset-ipv6-hy2`，独立标签 `v3.10.2-preset-ipv6-hy2`。旧 `v3.10.2-preset`、v3.6.0 视频版及其安装包保持不变。

## 一键部署

在 Linux VPS 上以 root 执行：

```bash
bash <(curl -fLsS https://raw.githubusercontent.com/kjxv/3x-ui/v3.10.2-preset-ipv6-hy2/deploy/preset/install.sh)
```

安装包、面板、安装脚本和管理脚本来自 `kjxv/3x-ui` 的本标签，下载后强制验证 SHA256。包内包含校验过的固定 Xray 内核。

## 首次部署生成的节点

| 名称 | 配置 | 默认出站 |
| --- | --- | --- |
| 自建-直连 | VLESS + TCP + REALITY / Vision，IPv4 分享地址 | VPS 直连 |
| 自建-住宅 | VLESS + TCP + REALITY / Vision | 住宅 Socks5 出站“默认” |
| 自建-IPv6直连 | VLESS + TCP + REALITY / Vision，IPv6-only 监听，独立 IPv6 分享地址 | VPS 直连 |
| 自建-HY2 | Hysteria 2 + QUIC/UDP + TLS | VPS 直连 |

四个入站共用一个邮箱为 **01** 的客户端及同一订阅 ID。UUID、HY2 认证密码、各 REALITY 密钥及 Short ID 都随机生成。VLESS 最小客户端版本为 1.0.0。订阅服务及 Clash/Mihomo 订阅默认开启，节点备注不含个人名称。

REALITY 继续使用面板的目标扫描器，筛选可用的非微软候选，再选延迟较低的目标。客户端 SNI 只包含选中的域名。HY2 不使用 REALITY 或该 SNI，它使用 TLS 证书所覆盖的名称。

### IPv6 条件

自动选择服务器已启用网卡上的公网 IPv6，排除 `fe80::`、`fd00::`、回环和 IPv4。分享地址不会误用登录面板时的 IPv4。VPS 必须事先由服务商和操作系统配置好 IPv6；此安装器不会替你创建 IPv6 隧道、修改系统网络或自动开通服务商地址。

若未发现公网 IPv6，仍创建该入站，但**禁用并从订阅排除**，在安装结果中明确提示。完成服务器 IPv6 配置后，在面板填写真实的 IPv6 分享地址、启用入站并取消“从订阅中排除”。使用 IPv6 节点的电脑或手机也必须有可用的 IPv6 网络。IPv6 接入不强制改变网站最终看到的出口地址族。

### HY2 条件

HY2 复用安装时配置的面板 TLS 证书及密钥文件，SNI 从证书覆盖的域名或 IP 中选择，保持正常证书校验，不生成跳过验证的订阅配置。证书续期继续使用现有文件及面板的重启机制。

默认安装流程申请 IP 证书或配置有效域名证书后，HY2 自动启用。若选择跳过 TLS，HY2 入站会禁用并从订阅排除，其他节点照常创建。后续可在该入站的安全设置中选择有效证书和对应 SNI，再启用并取消订阅排除。证书文件存在但无效、过期或密钥不匹配时，首次初始化明确报错并回滚，保留待重试标记。

### 端口与住宅出站

四个节点选择互不重复的随机空闲端口；VLESS 检查 TCP 占用，HY2 检查 UDP 占用。请在 VPS 防火墙和云安全组按安装结果放行：**VLESS 是 TCP，HY2 是 UDP**，同时保留面板和订阅端口。安装结果写入 `/etc/x-ui/preset-result.json`，权限 600，含新增端口及启用状态。

住宅节点沿用原版设置：登录后编辑 Xray 出站“默认”，填写住宅 Socks5 地址、端口和认证信息。未填写或不可达时，住宅节点失败，不回退到 VPS 出口；其他直连节点独立使用。供应商采用 IP 白名单时，将 VPS 的相应出口 IP 加入白名单。

## 可选参数

- `XUI_PRESET_PUBLIC_ADDRESS`：IPv4 节点和 HY2 的分享地址，可为 IP 或域名。
- `XUI_PRESET_IPV6_ADDRESS`：明确指定服务器的公网 IPv6，不能带方括号、端口、`/64` 或 `%接口编号`；须由你确保该地址已配置可用。
- `XUI_PRESET_DIRECT_PORT`、`XUI_PRESET_RESIDENTIAL_PORT`：原有 VLESS TCP 端口。
- `XUI_PRESET_IPV6_PORT`：新增 IPv6 VLESS TCP 端口。
- `XUI_PRESET_HY2_PORT`：HY2 UDP 端口。
- `XUI_PRESET_REALITY_SERVER_NAME`：指定已验证可用的非微软 REALITY 目标，跳过自动扫描。

## 已有面板和升级

预设只在首次空面板安装时执行，创建记录、路由、客户端、设置和完成标记在同一事务内提交。失败可修复后重试。

**在已有面板上运行新版安装器会保留已有配置，不自动追加节点。** 旧预设的完成标记同样受保护；已删除节点不会被重新创建，订阅 ID、UUID、住宅认证和用户修改不会被重置。本标签用于新部署时自动得到四个节点。

## Docker

```bash
docker build -t ghcr.io/kjxv/3x-ui:v3.10.2-preset-ipv6-hy2 https://github.com/kjxv/3x-ui.git#codex/v3.10.2-preset-ipv6-hy2
docker run -d --name 3x-ui --network host --cap-add=NET_ADMIN --cap-add=NET_RAW -v "$PWD/db:/etc/x-ui" ghcr.io/kjxv/3x-ui:v3.10.2-preset-ipv6-hy2
```

容器首次启动没有证书时，HY2 保留为待配置状态。容器使用 HY2 时，需要持久挂载有效证书文件，并在面板设置对应路径和 SNI。

## 验证与维护

发布前检查首次初始化和事务回滚、重复安装保护、IPv6 REALITY 实际连接、验证证书的 HY2 实际连接、住宅 Socks5 路由与失败隔离、Clash/Mihomo 四节点订阅、HTTPS 安装和多平台安装包。只有全部必要检查通过后，才发布本标签及完整安装包。
