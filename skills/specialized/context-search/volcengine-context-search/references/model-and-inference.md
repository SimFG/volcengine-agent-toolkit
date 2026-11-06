# 模型和推理服务

这是平台共享资源，不附加 SceneType。以下接口均为 POST；请求字段保留 PascalCase。

## 模型

- `ListAIModel`：`{Project,PageNumber,PageSize,Name?,Type?,Types?,ModelType?,DeploymentId?,Id?,SupportServerless?,SortField?,SortOrder?}`。
- `GetAIModel` / `GetAIModelRelations`：`{Project,Id}`。
- `CreateAIModel`：`{Project,Name,Description?,ModelType?,ModelPath?}`。
- `UpdateAIModel`：`{Project,Id,Description?,ModelType?,ModelPath?}`。
- `DeleteAIModel`：`{Project,Id}`。
- `ListArkModelVersion`：`{FoundationModelName?,FoundationModelId?,PageNumber,PageSize,Filter?,SortOrder?,SortBy?}`。

模型来源 USER / SYSTEM / ARK 不等同于推理服务的 PurchaseType。只修改用户自建模型，删除前读取 Deployments 和场景关联，防止悬空引用。模型类型如 LLM、MultimodalEmbedding、TextEmbedding、Rerank、ASR，以响应为准。

## 推理服务

- `ListAIDeployment`：`{Project,PageNumber,PageSize,Name?,Status?,Type?,ModelType?,PurchaseType?,ModelName?,EndpointId?,IsBuiltin?,SortField?,SortOrder?}`。
- `GetAIDeployment` / `GetAIDeploymentRelations`：`{Project,Id}`。
- `CreateAIDeployment`：根据所选 PAAS/SERVERLESS 契约提交完整 body。
- `CreateArkDeployment`：`{Project,Name,FoundationModelId,ArkModelVersion,Description?,RateLimit?,Moderation?}`。
- `UpdateAIDeployment`：`{Id,Description?,RateLimit?,Replicas?,ResourceSpec?,Nodes?,Models?,ForceRestart?}`，按当前服务类型核实适用字段。
- `StartAIDeployment`：`{Id,Replicas?,ResourceSpec?,EnableHA?}`。
- `StopAIDeployment` / `DeleteAIDeployment`：`{Project,Id}`。
- `CheckBuiltinDeployment` / `DeployBuiltinDeployment`：`{Project,Models:[{Name,Version}]}`。
- `DescribeArkEndpointPrice`：`{FoundationModelName?,FoundationModelId?,ServerlessType?}`。
- `GetArkEndpointUsage`：`{ProjectName,Id,Interval,StartTime,EndTime}`。

PAAS 使用独占实例，确认模型支持 SupportPaas 及规格/副本/网络；SERVERLESS 确认 SupportServerless，且只选一个模型。不得自动开通内置模型或创建 Ark 端点，这可能触发费用。用户确认模型、来源、限流、规格和费用后才写。

仅 RUNNING 的服务可供场景引用；创建响应 Id 不等于运行成功。更新运行中的 PAAS 可能重启，明确中断影响；Serverless 更新不要携带不适用的实例字段。删除前读取服务关联。

## API Key 与准备资源

- `ListMLApiKeys`：`{Project,PageNumber,PageSize,FilterArkApiKey,Encrypt}`。
- `GetMLApiKey` / `DeleteMLApiKey`：`{Id}`。
- `CreateMLApiKey`：`{Project,Name}`。

Key 是敏感响应值，默认脱敏；有明确后续请求时通过受控原始响应文件传递，不展示到回答或 Git。创建/删除须明确授权。VPC、可用区、规格等只读接口见 scene-common.md。
