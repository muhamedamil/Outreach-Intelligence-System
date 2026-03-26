# 🌌 Antigravity Outreach Intelligence System

**The 0.01% Standard Multi-Agent Lead Research & Outreach Pipeline.**

Antigravity is a high-performance, resilient AI pipeline designed to transform raw lead lists into deeply researched business intelligence and hyper-personalized outreach campaigns. It bypasses traditional scraping barriers using advanced AI synthesis and multi-agent coordination.

---

## 🏗️ System Architecture

```mermaid
graph TD
    A[Excel Upload] --> B[Orchestrator]
    B --> C[Researcher Agent]
    B --> D[Contact Finder Agent]
    B --> E[Outreach Writer Agent]
    
    C --> F[Tavily Advanced Search]
    D --> G[Multi-Directory Scraper]
    D --> H[AI Answer Synthesis]
    
    E --> I[Hook-Value-CTA Prompting]
    
    I --> J[Premium Dashboard UI]
```

## ✨ Key Features

- **🛡️ "Very Strict" Contact Discovery:** Multi-layer validation that filters out generic emails (a@gmail.com) and enforces valid 10-digit phone normalization.
- **🔍 Tavily Advanced Integration:** Leverages AI-synthesized answers and deep research depth to bypass "Click to Reveal" bot protection on high-authority sites.
- **🎨 Premium Dashboard UI:** A stunning, data-dense interface built with Inter & Outfit typography, glassmorphism design, and real-time processing stats.
- **⚡ High-Concurrency Processing:** Built with FastAPI and advanced async patterns to handle large lead lists (50+ rows) with zero system crashes.
- **📱 One-Click Outreach:** Integrated WhatsApp direct links and a "Copy Terminal" for rapid manual outreach.

---

## 🚀 Getting Started

### 1. Prerequisites
- Python 3.10+
- Tavily API Key ([Get one here](https://tavily.com/))
- LLM API Key (OpenAI/Groq/OpenRouter)

### 2. Installation
```bash
# Clone the repository
git clone https://github.com/your-repo/outreach-ai-system.git
cd outreach-ai-system

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Environment Setup
Create a `.env` file in the root directory (refer to `.env.example`):
```env
LLM_API_KEY=sk-...
TAVILY_API_KEY=tvly-...
```

### 4. Running the Application
```bash
uvicorn app.main:app --reload
```
Visit `http://127.0.0.1:8000` to access the dashboard.

---

## 📁 Project Structure

| Directory | Responsibility |
|-----------|----------------|
| `app/agents` | Brain logic (Researcher, Contact Finder, Writer). |
| `app/orchestrator` | Parallel execution and error handling logic. |
| `app/services` | Base scrapers, LLM clients, and File processors. |
| `app/api` | FastAPI routes and request handling. |
| `frontend/` | Dashboard UI (HTML/CSS/JS). |

---

## 🛠️ Configuration (Strictness & Timeouts)

- **Agent Timeout:** 300s (to allow deep research on slow directory sites).
- **API Timeout:** 3600s (global lifespan for large Excel batch processing).
- **Scraper Headers:** Mimics modern Chrome browsers to minimize bot detection.

---

## 🤝 Contribution
"0.01% Engineering" means code that is readable, resilient, and ready for production. Pull requests are welcome for any architectural improvements.

**Built with ❤️ for the future of AI Outreach.**
