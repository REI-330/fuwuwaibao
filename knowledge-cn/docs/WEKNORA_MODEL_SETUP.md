# WeKnora embedding 接入清单

当前 Lite 服务已能启动，但 `/api/v1/models` 为空。必须先登记一个真实 embedding 服务，才能把审核后的候选转换为 chunks。

## 推荐配置方式

在 WeKnora 源码目录复制 `config/builtin_models.yaml.example` 为 `config/builtin_models.yaml`，将示例中的 `builtin_models` 改为真实服务配置。不要把 API key 写入 Git：使用环境变量 `EMBEDDING_API_KEY`。

```yaml
builtin_models:
  - id: builtin-embedding-default
    type: Embedding
    source: remote
    is_default: true
    name: ${EMBEDDING_MODEL_NAME}
    parameters:
      base_url: ${EMBEDDING_BASE_URL}
      api_key: ${EMBEDDING_API_KEY}
      provider: generic
      embedding_parameters:
        dimension: 1536
        truncate_prompt_tokens: 0
```

变量值必须由实际供应商提供；`dimension` 必须与模型真实输出一致。重启 Lite 后执行：

```powershell
$env:WEKNORA_TOKEN = '<登录后 token>'
python acquisition/check_weknora.py
```

只有输出 `model_count > 0` 后，才执行审核清单导入、`reparse`、chunk 计数和检索 Top-5 评估。模型未配置时，脚本会非零退出，不能把健康检查当作检索验收。
