import requests
import json

response = requests.post(
  url="https://openrouter.ai/api/v1/embeddings",
  headers={
    "Authorization": "Bearer <OPENROUTER_API_KEY>",
    "Content-Type": "application/json",
    "HTTP-Referer": "<YOUR_SITE_URL>", # Optional. Site URL for rankings on openrouter.ai.
    "X-OpenRouter-Title": "<YOUR_SITE_NAME>", # Optional. Site title for rankings on openrouter.ai.
  },
  data=json.dumps({
    "model": "qwen/qwen3-embedding-8b",
    "input": "Your text string goes here",
    # "input": ["text1", "text2", "text3"], # batch embeddings also supported!
    "encoding_format": "float"
  })
)
