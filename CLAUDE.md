# Circuits Tutor

## Product Vision
An AI-powered circuits tutor for ECE students. Acts as a personalized study 
assistant — identifies the hardest part of a problem, guides methodology, 
and adapts to question complexity. Gives direct answers only when explicitly 
asked, like a real tutor would.

## Tech Stack
- Frontend: React
- Backend: Python (FastAPI)
- AI: Anthropic API (claude-opus-4-8 for hardest/image problems, 
  claude-sonnet-5 for moderate complexity, claude-haiku-4-5 for follow-ups)
- Knowledge base: ChromaDB (local)
- PDF parsing: PyMuPDF
- YouTube transcript ingestion: youtube-transcript-api

## Which Model Drives Which Build Task
(models used to BUILD the app, separate from the routing logic inside the app)
- Planning & architecture decisions: Fable in claude.ai (already done — this doc)
- Section 1 (FastAPI scaffolding, endpoints, boilerplate): Sonnet in Claude Code — fast, cheap, this is well-trodden code
- Section 2 (routing classifier + summary buffer): Opus in Claude Code — this is the novel logic where design judgment matters most
- Section 3 (RAG pipeline): Opus — embedding/chunking decisions are easy to get subtly wrong
- Section 4 (React frontend): Sonnet — standard UI work
- Section 5 (YouTube recs): Haiku or Sonnet — simple mapping logic
- Use /model inside Claude Code to switch between sections

## Model Routing Logic (inside the app)
- Opus: image attached, multi-step derivation, hardest conceptual problems
- Sonnet: moderate complexity, new topic questions without images
- Haiku: short follow-ups, clarifications, single definitions

## Summary Buffer
After every turn (Opus, Sonnet, or Haiku), Haiku compresses conversation 
history into a structured context blob instead of passing full history on 
follow-ups. Updating on every turn — including Haiku follow-ups — keeps the 
injected buffer from going stale across a run of clarifications. (See 
DECISIONS.md → "Summary on every turn" for the rationale.)

Blob schema:
{
  "topic": "...",
  "concepts_covered": [...],
  "student_struggle_points": [...],
  "last_2_messages": [...]
}

## V1 Build Plan

### 1. Backend (FastAPI)
Install: pip install fastapi uvicorn anthropic python-multipart python-dotenv
- API endpoint that accepts text + optional image
- Classifier that routes to Opus, Sonnet, or Haiku
- Summary buffer logic
- Returns structured response to frontend

### 2. Model Routing + Summary Buffer
Install: nothing additional
- Classifier function: checks for image attachment + question complexity keywords
- Summary buffer: Haiku compresses conversation history into structured blob after each Opus/Sonnet turn

### 3. RAG Knowledge Base
Install: pip install chromadb pymupdf sentence-transformers youtube-transcript-api
- Ingest textbook, past exams, homework solutions as PDFs
- Ingest YouTube lecture transcripts via youtube-transcript-api
- PyMuPDF parses PDFs into text chunks
- ChromaDB stores embeddings locally, tagged by source type (pdf/video)
- On each query, retrieve relevant chunks and inject into system prompt

### 4. Frontend (React)
Install: npx create-react-app circuits-tutor-frontend or npm create vite@latest
- Text input + image upload component
- Conversation display
- Calls FastAPI backend

### 5. YouTube Recommendations
Install: nothing additional
- When student asks for resources, match topic to curated playlist map
- Return relevant video links based on topic categorization

## Current State
- Anthropic API call working locally
- Nothing else implemented yet

## Running Locally
- Backend: uvicorn main:app --reload (port 8000)
- Frontend: npm start (port 3000)
