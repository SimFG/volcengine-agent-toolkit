# 多模态搜索

SceneType=MULTIMODAL_SEARCH。列表 include 为 true / false / true（Psr / AgenticSearch / MultimodalSearch）。创建、详情、配置、数据、网络和实例使用 scene-common.md 的通用接口。

## 入库

先确认用户文件、场景与 Project，查询入库配置。以准确字节数申请上传：

`GetMultimodalUploadFileInfo` POST：

```json
{
  "Project": "default",
  "SceneId": "<用户已确认的场景 ID>",
  "SceneType": "MULTIMODAL_SEARCH",
  "UuidPrefix": "<本次上传前缀>",
  "SseEnabled": false,
  "FileInfos": [{"Name": "asset.bin", "Size": 2048}]
}
```

读取上传信息，将预签名地址与 Headers 作为本地受控文件输入 upload 子命令；然后 `AddSceneData` POST 注册：

```json
{
  "Project": "default",
  "SceneId": "<场景 ID>",
  "SceneType": "MULTIMODAL_SEARCH",
  "Type": "LOCAL",
  "FileInfoList": ["{\"file_name\":\"asset.bin\",\"file_path\":\"<上传返回的 tos 路径>\"}"],
  "TosConfig": {"Prefix": "<本次上传前缀>"}
}
```

FileInfoList 是 JSON **字符串**数组，元素字段为 file_name 与 file_path；不要使用旧媒体的 vector_source_image，不传 IngestConfig。对象路径仅供请求。注册成功后查询数据状态至 SUCCEEDED 或 FAILED，不能把 PUT 成功等同入库成功。

## 任务与检索

- `ListMultimodalTasks` GET：Project、SceneId、SceneType=MULTIMODAL_SEARCH、Limit、Offset、Status?。
- `ListSceneData` / `GetSceneData` / `ListSceneDataChunk`：见 scene-common.md，WithImageData=false。
- `TestSceneInstance` POST：

```json
{
  "Project": "default", "SceneId": "<场景 ID>",
  "SceneType": "MULTIMODAL_SEARCH", "Environment": "PROD",
  "Description": "<用户检索问题>", "Filter": [],
  "PageNumber": 1, "PageSize": 10, "WithImageData": false, "IsDemo": false
}
```

检索仅对 RUNNING 场景执行。使用普通 JSON 响应接口，不替换成旧 RAG 的 SSE 接口。Description、过滤条件和返回量应对应用户需求。

## 变更边界

Embedding、网络、规格、切片和检索参数必须由用户明确或从只读候选中选定。多模态 `CreateSceneVersion` 未提供可依赖的成功契约，不默认调用它发布；使用实例状态和日志核验。写失败先读取任务/数据状态，避免重复注册。
