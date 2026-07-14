---
tasks:
- embedding
license: Apache License 2.0
---
FlagEmbedding can map any text to a low-dimensional dense vector which can be used for tasks like retrieval, classification, clustering, or semantic search. And it also can be used in vector databases for LLMs.

- dimensions: 512
- max_tokens: 512
- language: zh

# Usage

###  Start a local instance of Xinference
```bash
xinference -p 9997
```

### Launch and inference
```python
from xinference.client import Client

client = Client("http://localhost:9997")
model_uid = client.launch_model(model_name="bge-small-zh-v1.5", model_type="embedding")
model = client.get_model(model_uid)

model.create_embedding("write a poem.")
```

