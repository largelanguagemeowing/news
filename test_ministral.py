import json
import requests
import sys


def test_classify(title, body):
    url = "http://localhost:1234/api/v1/chat"
    system_prompt = "You are a news classifier. Classify the article into exactly one of the following categories: ai-models, product, research, agents, safety, security, policy, infrastructure, funding, general. Output only the category name."
    # Combine title and body, but we may need to truncate if too long
    text = f"Title: {title}\n\nBody: {body}"
    data = {
        "model": "mistralai/ministral-3-3b",
        "system_prompt": system_prompt,
        "input": text,
    }
    try:
        response = requests.post(url, json=data, timeout=30)
        response.raise_for_status()
        result = response.json()
        # Extract the output text
        output = result.get("output", [])
        if output and len(output) > 0:
            # Assuming the first output is a message with content
            content = output[0].get("content", "").strip()
            return content
        else:
            return "No output"
    except Exception as e:
        return f"Error: {e}"


if __name__ == "__main__":
    # Load the first article
    with open("/home/udit/Dev/personal/news-aggregator/data/status/articles.json") as f:
        articles = json.load(f)
    article = articles[0]
    title = article.get("title", "")
    body = article.get("body", "")
    print(f"Title: {title}")
    print(f"Body length: {len(body)}")
    # Try with full body
    print("\n--- Testing with full body ---")
    result = test_classify(title, body)
    print(f"Classification: {result}")
    # Try with first 500 chars
    print("\n--- Testing with first 500 chars ---")
    result = test_classify(title, body[:500])
    print(f"Classification: {result}")
    # Try with first 1000 chars
    print("\n--- Testing with first 1000 chars ---")
    result = test_classify(title, body[:1000])
    print(f"Classification: {result}")
