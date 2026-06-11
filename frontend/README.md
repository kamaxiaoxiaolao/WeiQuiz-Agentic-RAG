# WeiQuiz Frontend

Vue 3 frontend for the WeiQuiz Agentic RAG application.

## Stack

- Vue 3
- TypeScript
- Vite
- Pinia
- Tailwind CSS
- lucide-vue-next

## Development

```bash
npm install
npm run dev
```

The frontend expects the FastAPI backend to be available from the same origin in production or proxied during local development.

## Build

```bash
npm run build
```

## Main Views

- `LoginView.vue`: authentication entry.
- `ChatView.vue`: chat workspace with sessions, knowledge-base panel, and debug panel.
- `AdminView.vue`: admin operations for users and audit-related workflows.
