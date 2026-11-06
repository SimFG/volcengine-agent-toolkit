# 请求协议

## 调用与输入

唯一入口为 `node <安装目录>/scripts/ctxsearch.mjs`，默认子命令 `call`。所有路径相对当前用户工作目录解析，脚本路径必须以实际安装目录为准。

`call` 接受：

- `--action <Action>`（必填）、`--method GET|POST`（默认 GET）。GET 不接受 body。
- `--query <JSON>`、`--query-file <文件>`、`--query-stdin` 三选一；`--query -` 等同 stdin。
- `--param key=value` 可重复，覆盖 query 同名参数。query 值只允许标量和标量数组，null 忽略，数组展开为重复 key。业务 query 不得覆盖 `Action`、`Version`。
- `--body <JSON>`、`--body-file <文件>`、`--body-stdin` 三选一；`--body -` 等同 stdin。body 必须是 JSON 对象。
- query 和 body 不得同时从 stdin 读取。
- `--region` 默认 `cn-beijing`；`--timeout` 默认 60000ms，上限 600000ms。
- `--compact` 输出紧凑 JSON；`--output-file <新文件>` 保存原始响应。已存在的目标不会被覆盖，文件权限 0600。

正式 Host、Service 和 API Version 固定在脚本中；不允许通过 CLI、额外 header 或环境变量改变路由。不要尝试内部服务名。

## 凭据

依次检查三组环境变量：

1. `CTXSEARCH_ACCESS_KEY_ID` 与 `CTXSEARCH_SECRET_ACCESS_KEY`。
2. `CTXSEARCH_TEST_AK` 与 `CTXSEARCH_TEST_SK`（兼容名称，同样访问正式服务）。
3. `VOLC_ACCESSKEY` 与 `VOLC_SECRETKEY`。

跳过完全未配置的一组；半组配置时报错，不继续查下一组。临时凭据为 `CTXSEARCH_SESSION_TOKEN` 或 `VOLC_SESSION_TOKEN`。仅继承用户已经配置的进程环境，不读取本地登录文件、不自动 source 未知凭据文件。

## 签名与网络

使用 Volcengine V4 HMAC-SHA256：

- 请求路径 `/`，query 包含 `Action`、`Version` 与业务字段；字段名保持 PascalCase。
- 规范 query 以 RFC3986 转义后的 key/value 排序。空数组不发送，数组值重复 key。
- `X-Date` 为 UTC 时间，临时凭据使用 `X-Security-Token`。JSON body 以 UTF-8 序列化，带 `X-Content-Sha256`。
- `X-*` 签名头按小写名称排序；`Content-Type` 与 `Content-Length` 不参与签名。
- 派生密钥：SK → 日期 → Region → `ctxsearch` → `request`，每轮传递原始 HMAC 字节。
- URL 以 `URLSearchParams` 序列化；仅发 HTTPS，不跟随重定向。整体超时包含建立连接和读取响应，响应最多 32 MiB。
- 不实现脚本内代理，不传代理开关；网络受限时检查用户网络配置。

## 上传

先通过对应 Action 获取上传信息，再用返回的地址和 Headers。不要重用过期预签名地址。

```bash
node scripts/ctxsearch.mjs upload --url-file ./upload-url.txt \
  --file ./asset.bin --headers-file ./upload-headers.json
```

- URL 文件为仅含预签名 HTTPS URL 的 UTF-8 文本。
- Headers 文件为字符串值 JSON 对象；从接口返回的 `{Key, Value}[]` 转换，不要填凭据或 Host。
- 上传固定 PUT，自动设置 `Content-Length`。不签 OpenAPI，不回显上传地址、header 或服务器错误原文；只返回 `ok`、HTTP 状态和字节数。
- 将以上临时文件放在受控目录，不提交仓库。

## 输出与失败

退出码：0 = HTTP 2xx 且无 API Error；1 = 非 2xx 或 `ResponseMetadata.Error`；2 = 参数、凭据、文件或网络错误。JSON 响应成功写 stdout，失败写 stderr；默认脱敏。非 JSON 2xx 可为 SSE/纯文本，保留文本结构，不因此判失败；它不是场景列表的有效验收响应。非 JSON 数据可能含业务隐私，只显示完成任务需要的摘要。

`--output-file` 是未经脱敏的原始响应，只供下一步读取。用户只要列表时不需要保存原始响应。

常见错误：

- `SignatureDoesNotMatch`：核对同源 AK/SK、SessionToken 和时间，确认地域；不要切换服务。
- 权限错误：确认账号和目标 Project/Scene 的权限。
- 资源不存在：核对 Id、Project、SceneType 以及对应详情接口。
- `InvalidActionOrVersion`：本服务不支持该契约，不猜其他 Action。
- 超时/断连：写操作先复核现状；只读操作必要时有限重试，不无限等待。

## 只读冒烟验收

按 `scene-common.md` 设置 ListScene 的 include flags。返回 JSON、`Result.Items` 数组、无 Error、退出码 0 即通过（数组可以为空）。保留分页范围和 Service/Region，不展示凭据，不通过创建资源验证。
