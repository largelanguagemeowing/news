import json
import requests
import sys


def classify_with_ministral(title, body):
    url = "http://localhost:1234/api/v1/chat"
    system_prompt = "You are a news classifier. Classify the article into exactly one of the following categories: ai-models, product, research, agents, safety, security, policy, infrastructure, funding, general. Output ONLY the category name, nothing else."
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
        output = result.get("output", [])
        if output and len(output) > 0:
            content = output[0].get("content", "").strip()
            # Extract just the category name (first word or clean up)
            content = content.lower().strip()
            # Remove any extra text, just take first reasonable word
            for word in content.split():
                if word in [
                    "ai-models",
                    "product",
                    "research",
                    "agents",
                    "safety",
                    "security",
                    "policy",
                    "infrastructure",
                    "funding",
                    "general",
                ]:
                    return word
            # If no exact match, return first word cleaned
            cleaned = "".join(c for c in content.split()[0] if c.isalnum() or c == "-")
            return (
                cleaned
                if cleaned
                in [
                    "ai-models",
                    "product",
                    "research",
                    "agents",
                    "safety",
                    "security",
                    "policy",
                    "infrastructure",
                    "funding",
                    "general",
                ]
                else "general"
            )
        else:
            return "general"
    except Exception as e:
        print(f"Error classifying: {e}")
        return "error"


def main():
    # Load articles and existing labels
    with open("/home/udit/Dev/personal/news-aggregator/data/status/articles.json") as f:
        articles = {a["article_id"]: a for a in json.load(f)}

    with open(
        "/home/udit/Dev/personal/news-aggregator/data/classifier/weak_labels.json"
    ) as f:
        weak_labels = {l["article_id"]: l for l in json.load(f)}

    # Test on first 10 articles
    mismatches = 0
    total = min(10, len(articles))

    print("Comparing first 10 articles:")
    print("=" * 80)

    for i, (aid, article) in enumerate(list(articles.items())[:total]):
        title = article.get("title", "")
        body = article.get("body", "")

        # Get existing label
        existing_label = weak_labels.get(aid, {}).get("label", "none")

        # Get Ministral label
        ministral_label = classify_with_ministral(title, body[:2000])  # Limit body size

        match = "✓" if existing_label == ministral_label else "✗"
        if existing_label != ministral_label:
            mismatches += 1

        print(f"{match} ID: {aid}")
        print(f"  Title: {title[:60]}...")
        print(f"  Existing: {existing_label:12} | Ministral: {ministral_label}")
        print(f"  Body preview: {body[:100].replace(chr(10), ' ')}...")
        print()

    print(f"Results: {mismatches}/{total} mismatches ({mismatches / total * 100:.1f}%)")


if __name__ == "__main__":
    main()
