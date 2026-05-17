import json
import requests
import re


def classify_with_reasoning(title, body):
    url = "http://localhost:1234/api/v1/chat"
    system_prompt = """You are an expert news classifier for AI/technology articles. 
    Your task is to classify the article into exactly one of these categories:
    - ai-models: About new AI models, model releases, model comparisons, model capabilities
    - product: About AI products, tools, applications, software releases, consumer-facing AI tools
    - research: About AI research papers, academic studies, scientific findings, breakthrough research
    - agents: About AI agents, autonomous systems, agent frameworks, AI that can perform tasks independently
    - safety: About AI safety, alignment, ethics, risks, responsible AI development
    - security: About AI security, vulnerabilities, attacks, defense, protecting AI systems
    - policy: About AI policy, regulation, legislation, government involvement in AI
    - infrastructure: About AI infrastructure, hardware, systems, scaling, computing resources for AI
    - funding: About AI funding, investments, acquisitions, money flowing into AI companies
    - general: General AI news that doesn't clearly fit into the above categories
    
    First, analyze the article's main topic and key points. Then select the single best category.
    Output your reasoning followed by the final label in this format:
    REASONING: [Your analysis here]
    LABEL: [exactly one category name]"""

    # Limit body to avoid token issues but keep meaningful content
    limited_body = body[:2000] if len(body) > 2000 else body
    input_text = f"Title: {title}\n\nBody: {limited_body}"

    data = {
        "model": "mistralai/ministral-3-3b",
        "system_prompt": system_prompt,
        "input": input_text,
        "temperature": 0.1,  # Lower temperature for more consistent outputs
        "max_tokens": 500,
    }

    try:
        response = requests.post(url, json=data, timeout=30)
        response.raise_for_status()
        result = response.json()
        output = result.get("output", [])
        if output and len(output) > 0:
            content = output[0].get("content", "").strip()

            # Extract label using regex
            label_match = re.search(
                r"LABEL:\s*(ai-models|product|research|agents|safety|security|policy|infrastructure|funding|general)",
                content,
                re.IGNORECASE,
            )
            if label_match:
                return label_match.group(1).lower(), content
            else:
                # Fallback: look for any category name in the content
                content_lower = content.lower()
                for category in [
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
                    if category in content_lower:
                        return category, content
                return "general", content
        else:
            return "general", "No output"
    except Exception as e:
        return f"error: {e}", ""


if __name__ == "__main__":
    # Test on a few articles
    with open("/home/udit/Dev/personal/news-aggregator/data/status/articles.json") as f:
        articles = json.load(f)

    print("Testing with reasoning approach:")
    print("=" * 80)

    for i in range(min(5, len(articles))):
        article = articles[i]
        title = article.get("title", "")
        body = article.get("body", "")

        label, reasoning = classify_with_reasoning(title, body[:1500])
        print(f"Article {i + 1}: {title}")
        print(f"Predicted label: {label}")
        print(f"Reasoning: {reasoning[:200]}...")
        print()
