# 个性化搜推（PSR）

SceneType=PSR，三个列表 include flags 全为 true。只进行读取，不能借通用 Action 实现未开放的写操作。

- `ListScene` GET：Project、SceneType=PSR、Name?、PageNumber、PageSize 与 include flags；Result.Items/Total。
- `GetPsrScene` GET：Id、Project、IsDemo=false。
- `GetCurrentPsrServe` POST `{Project,SceneId}`。
- `GetAssistantSession` POST `{SceneId}`；消息按敏感业务数据处理。
- `ListPsrDataset` GET：Project、SceneId、PageNum、PageSize、DatasetNameKey；注意是 PageNum，不是 PageNumber。
- `ListPsrPipelines` POST `{Project,SceneId,PageNumber,PageSize,SortField:"CreateTime",SortOrder:"DESC"}`。

详情中的模型凭据、存储、网络配置不展开到回答。模型与部署的共享读取见 model-and-inference.md。

创建/初始化/编辑/发布/启停/删除 PSR、绑定和上传不在本插件的已知写契约内；明确告知限制并建议使用控制台，不猜 Action，也不切換服务。
