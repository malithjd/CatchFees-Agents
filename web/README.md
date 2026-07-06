# CatchFees Web UI

This is the Vite + React SPA for the CatchFees Agent platform.

## Architecture
- Single Page Application
- No routing libraries (uses smooth scrolling between sections)
- Pure React State (no external stores)
- Strict Neumorphic design system with plain CSS

## Development

Run the frontend development server (defaults to port 5173):
```bash
npm run dev
```

Run the backend API server in a separate terminal:
```bash
cd ..
uv run uvicorn --app-dir src catchfees.server:app --port 8080
```

## Build

To compile for production:
```bash
npm run build
```
The compiled files will be located in the `dist` directory.
