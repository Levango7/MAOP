# MAOP Dashboard Enterprise

Vue 3 enterprise dashboard for the MAOP Multi-Agent Orchestration Platform.

## Quick Start

```bash
# Install dependencies
npm install

# Start dev server (http://localhost:9079)
npm run dev

# Build for production
npm run build

# Run tests
npm test

# Watch mode tests
npm run test:watch
```

## Architecture

```
src/
├── views/           # Route pages (19)
│   ├── Overview.vue       # Dashboard home with KPIs
│   ├── Agents.vue         # Agent management & monitoring
│   ├── Chat.vue           # Interactive chat with agents
│   ├── Monitor.vue        # Real-time system metrics
│   ├── Evolve.vue         # Evolution loop visualization
│   ├── Models.vue         # LLM provider management
│   ├── Tools.vue          # MCP tool registry
│   ├── VectorSearch.vue   # Semantic search interface
│   ├── ThreeLayerMemory.vue # Memory visualization
│   ├── ControlPanel.vue   # System control & config
│   ├── Settings.vue       # User settings
│   ├── RBAC.vue           # Role-based access control
│   ├── Audit.vue          # Audit log viewer
│   ├── Tenants.vue        # Multi-tenant management
│   ├── Users.vue          # User management
│   ├── Cost.vue           # Cost tracking & analytics
│   ├── Logs.vue           # System log viewer
│   ├── Search.vue         # Episodic memory search
│   └── Docs.vue           # In-app documentation
├── components/      # Reusable components (13)
│   ├── AppIcon.vue        # SVG icon system
│   ├── PageHeader.vue     # Standard page header
│   ├── Card.vue           # Content card
│   ├── StatCard.vue       # KPI stat card
│   ├── Badge.vue          # Status badge
│   ├── DataTable.vue      # Sortable data table
│   ├── TopBar.vue         # Navigation top bar
│   ├── AppFooter.vue      # Footer
│   ├── Toast.vue          # Toast notifications
│   ├── Skeleton.vue       # Loading skeleton
│   ├── EmptyState.vue     # Empty state placeholder
│   └── Segmented.vue      # Segmented control
├── composables/     # Vue composables (3)
├── stores/          # Pinia stores (4)
│   ├── api.js             # API client & endpoints
│   ├── ui.js              # UI state (theme, sidebar)
│   ├── realtime.js        # WebSocket real-time data
│   └── edition.js         # Edition detection (personal/enterprise)
├── i18n/            # Internationalization (18 locale files)
├── router/          # Vue Router config
├── styles/          # Global CSS
└── main.js          # App entry point
```

## Key Features

- **Real-time streaming** via WebSocket (SSE fallback)
- **Dual edition** support (Personal/Enterprise) with feature gating
- **i18n** with 18 language files (zh/en)
- **Chart.js** for data visualization
- **DOMPurify** for XSS-safe HTML rendering
- **Pinia** for state management
- **Lazy-loaded** routes for code splitting

## Development

### Adding a New View

1. Create `src/views/MyView.vue`
2. Add route in `src/router/index.js`
3. Add nav item in `src/nav.js`
4. Add i18n keys in `src/i18n/zh/view.js` and `src/i18n/en/view.js`

### Adding a New Component

1. Create `src/components/MyComponent.vue`
2. Export from `src/components/index.js`
3. Add test in `src/__tests__/MyComponent.test.js`

### State Management

Use Pinia stores for shared state:

```js
import { useApiStore } from '../stores/api';
const api = useApiStore();
```

### API Calls

All API calls go through the api store:

```js
const api = useApiStore();
const data = await api.get('/agents');
await api.post('/agents', { name: 'new-agent' });
```

## Testing

```bash
# Run all tests
npm test

# Watch mode
npm run test:watch

# Coverage report
npm run test:coverage
```

Tests use Vitest + Vue Test Utils + jsdom. Test files are co-located in `src/__tests__/`.

## Security

- All `v-html` renderings use DOMPurify sanitization
- CSP headers configured in `nginx.prod.conf`
- No secrets in frontend code — all via API proxy

## Build

Production build outputs to `dist/`. The build is configured via `vite.config.js`.

```bash
npm run build      # Output to dist/
npm run preview    # Preview production build
```