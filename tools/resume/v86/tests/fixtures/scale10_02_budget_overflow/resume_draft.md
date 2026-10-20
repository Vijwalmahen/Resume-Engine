Candidate Example
(555)-010-2000 | candidate@example.com | linkedin.com/in/candidate | github.com/example

EDUCATION

Example University — Example City, ST	Aug 2024 – Expected May 2026
Master of Science in Data Engineering, GPA: 3.80/4.00
Coursework: Distributed Data Systems, Data Pipeline Architecture, Database Internals, Applied Machine Learning
Example State College — Example City	Aug 2019 – 2023
Bachelor of Science in Applied Mathematics, GPA: 3.60/4.00
Coursework: Linear Algebra, Probability & Statistics, Numerical Methods, Optimization

PROFESSIONAL EXPERIENCE

Machine Learning Research Intern | Example Research Lab | Example City, USA (Remote)	Jan 2025 – May 2025
- Built a data pipeline that cleaned and merged 5 heterogeneous lab datasets into one training-ready corpus.
- Trained a baseline classification model and improved its validation F1 score from 0.71 to 0.84 through feature engineering.
- Wrote automated tests that reduced a recurring data-pipeline regression from happening roughly weekly to zero over 3 months.
- Presented findings in a lab-wide seminar and incorporated feedback into a revised experiment design.
AI Engineer Intern | Example Data Systems | Example City (Remote)	Jun 2025 – Aug 2025
- Built an internal tool that automated a manual data-labeling step, cutting labeling turnaround time from 2 days to 4 hours.
- Integrated a vector database into an internal search prototype and added evaluation scripts the team still uses.

PROJECTS

Project Meridian  —  Python, LangGraph, Qdrant, BM25, FastAPI, Docker, Redis
- Designed a production-shaped RAG architecture — versioned document ingestion, hybrid retrieval (BM25 + HNSW vector search with RRF fusion), and cross-encoder reranking — over a 40k-article internal support-documentation corpus with routine content updates.
- Built a quantitative evaluation harness (Recall@k, nDCG@k, MRR, LLM-judged faithfulness/correctness) that gates index promotion via alias-based blue/green deployment, then used it to validate a cost/latency optimization pass: 24% cheaper, 33% faster, with Recall@10 within 2 points of baseline (0.68 → 0.66).
- Added prompt-injection, scope, and PII guardrails with citation validation, blocking ~78% of a curated adversarial evaluation set; served via FastAPI + Redis at ~900 ms p50 end-to-end latency.
Project Nova — Personal Project  —  Python, LangGraph, ChromaDB, QLoRA, FastAPI, Docker, Redis
- Designed an end-to-end course-recommendation engine over an 8K-course catalog using hybrid dense+sparse retrieval (ChromaDB + BM25 with RRF) and a fine-tuned cross-encoder reranker, improving Recall@20 from 0.52 → 0.64 and nDCG@10 to 0.68.
- Built a self-distillation pipeline with LLM-as-judge to generate ~5K training pairs at near-zero cost, then fine-tuned a small open-weight cross-encoder (QLoRA) and bi-encoder; added LLM-powered tag enrichment and engagement-weighted personalization via LangGraph.
- Optimized for production with ONNX/int8 export, two-tier Redis caching, and HyDE expansion — reducing average latency to ~140 ms and LLM cost by ~30% — then deployed as a multi-container FastAPI service.

Project Horizon — Personal Project  —  Python, pandas, Prophet, Docker
- Built a time-series demand-forecasting model for a simulated regional logistics network, reducing forecast error (MAPE) from 19% to 11% versus a naive seasonal baseline.
- Packaged the forecasting model behind a weekly batch-scoring script that regenerated demand forecasts across 12 simulated distribution regions, containerized with Docker for consistent scheduling.

SKILLS

Languages: Python, C++, Java, SQL
Frameworks: PyTorch, TensorFlow, scikit-learn, Hugging Face, LangChain, LangGraph
Backend/API: FastAPI, Pinecone, MongoDB, REST APIs, FAISS, PostgreSQL, Git, Docker, Redis
