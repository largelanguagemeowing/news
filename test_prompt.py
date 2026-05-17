import json
import requests


def classify_with_ministral(title, body, use_few_shot=False):
    url = "http://localhost:1234/api/v1/chat"
    if use_few_shot:
        system_prompt = """You are a news classifier for AI/news articles. 
        Classify the article into exactly one of the following categories:
        - ai-models: About new AI models, model releases, model comparisons
        - product: About AI products, tools, applications, releases
        - research: About AI research papers, studies, findings
        - agents: About AI agents, autonomous systems, agent frameworks
        - safety: About AI safety, alignment, ethics, risks
        - security: About AI security, vulnerabilities, attacks, defense
        - policy: About AI policy, regulation, legislation, government
        - infrastructure: About AI infrastructure, hardware, systems, scaling
        - funding: About AI funding, investments, acquisitions, money
        - general: General AI news that doesn't fit above categories
        
        Output ONLY the category name, nothing else."""

        # Add a few examples
        examples = """


Examples:
Title: OpenAI releases GPT-5 with improved reasoning
Body: OpenAI today announced the release of GPT-5, their latest language model with improved reasoning capabilities...
Label: ai-models

Title: New AI-powered code assistant tool launched by GitHub
Body: GitHub has launched a new AI-powered code assistant that helps developers write code faster...
Label: product

Title: Researchers propose new technique for efficient transformer training
Body: A new paper from Stanford researchers proposes a technique that reduces training time for transformers by 40%...
Label: research

Title: AutoGPT framework enables autonomous AI agents for complex tasks
Body: The AutoGPT framework allows users to create autonomous AI agents that can perform complex tasks without human intervention...
Label: agents

Title: Study reveals potential risks of large language models in medical advice
Body: A recent study highlights the potential risks of using large language models for giving medical advice without proper oversight...
Label: safety

Title: Security researchers find vulnerability in popular AI inference server
Body: Security researchers have discovered a critical vulnerability in a widely used AI inference server that could allow remote code execution...
Label: security

Title: EU proposes new AI regulation act to govern high-risk AI systems
Body: The European Union has proposed a new regulation act that aims to govern the development and deployment of high-risk AI systems...
Label: policy

Title: NVIDIA announces new AI supercomputer for research labs
Body: NVIDIA today announced a new AI supercomputer system designed specifically for research labs working on large AI models...
Label: infrastructure

Title: Venture capital firm invests $200M in AI startup focused on healthcare
Body: A prominent venture capital firm has announced a $200M investment in an AI startup that focuses on healthcare applications...
Label: funding

Title: Weekly roundup of AI news and developments
Body: This week's roundup covers various AI news including new tool releases, research updates, and industry developments...
Label: general"""

        input_text = (
            f"{examples}\n\nNow classify this article:\nTitle: {title}\n\nBody: {body}"
        )
    else:
        system_prompt = "You are a news classifier. Classify the article into exactly one of the following categories: ai-models, product, research, agents, safety, security, policy, infrastructure, funding, general. Output ONLY the category name, nothing else."
        input_text = f"Title: {title}\n\nBody: {body}"

    data = {
        "model": "mistralai/ministral-3-3b",
        "system_prompt": system_prompt,
        "input": input_text,
    }
    try:
        response = requests.post(url, json=data, timeout=30)
        response.raise_for_status()
        result = response.json()
        output = result.get("output", [])
        if output and len(output) > 0:
            content = output[0].get("content", "").strip()
            content = content.lower().strip()
            # Extract first word that matches a category
            for word in content.split():
                word = word.strip(".,:;!?\"'")
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
            # If no match, return first word cleaned
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


if __name__ == "__main__":
    # Load the first few articles
    with open("/home/udit/Dev/personal/news-aggregator/data/status/articles.json") as f:
        articles = json.load(f)

    print("Testing with few-shot prompt:")
    print("=" * 80)
    for i in range(min(5, len(articles))):
        article = articles[i]
        title = article.get("title", "")
        body = article.get("body", "")
        print(f"Article {i + 1}: {title}")
        print(f"Body length: {len(body)}")

        # Test without few-shot
        label1 = classify_with_ministral(title, body[:1500], use_few_shot=False)
        print(f"  Direct prompt: {label1}")

        # Test with few-shot
        label2 = classify_with_ministral(title, body[:1500], use_few_shot=True)
        print(f"  Few-shot prompt: {label2}")
        print()
