# 存量旧场景

RAG、IMAGE_SEARCH、VIDEO_SEARCH 仅用于用户明确指定的存量查询/运维。新建知识库、图片和视频检索需求引导至 MULTIMODAL_SEARCH，不默认创建旧类型。

列表 include flags 和通用接口见 scene-common.md。保持真实 SceneType，不能把旧资源改名为多模态后继续操作。

## 入库与切片

存量上传申请使用 `GetIndexerUploadFileInfo` POST：`{UuidPrefix,SseEnabled:false,FileInfos:[{Name,Size}]}`。随后用 upload PUT，再 `AddSceneData` 注册；具体完整配置以原场景入库配置为准。

与多模态不同，旧媒体 FileInfoList 可能要求 file_path / vector_source_image，IngestConfig 可能包含 Prompt、MaxChunkDuration、SceneType。视频可能含 ASR。不要复制多模态参数至旧媒体，也不要凭空补模型端点。

`ListSceneData`、`GetSceneData`、`ListSceneDataChunk` 沿用通用身份字段；旧图片的 WithImageData=true，其他默认 false。

## 检索

- 存量 RAG：`TestSceneInstanceWithSSE` POST，返回 SSE；构造 body 前先读取草稿和存量测试契约，不猜嵌套 LLM/Rerank 配置。
- 存量图片/视频：`TestSceneInstance` POST，普通 JSON；媒体检索字段与多模态不一定相同，应使用用户现有请求或明确契约，不通过切换服务环境补救。

实例启停、规格和支持的版本发布使用通用 Action，但每次先读现状并确认中断/费用影响。没有明确旧接口 body 时先获取官方契约，不将不完整示例当可执行请求。

## 边界

`ListIndexerDataSource` GET：Project、NameKey、Type、PageNum、PageSize；`GetIndexerDataSource` GET：Id。旧 Indexer 数据源写接口不作为已知 OpenAPI Action 调用；需要时建议控制台。新 Agentic 数据源有独立写契约，见 agentic-search.md。
